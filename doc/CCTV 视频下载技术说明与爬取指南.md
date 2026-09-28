------

# CCTV 视频下载技术说明与爬取指南

本工程（`myDownload`）在针对 CCTV（央视网）流媒体视频进行抓取时，由于平台存在“双轨制”视频分发机制（标准 HLS 轨道与 H5E 混淆加密轨道），为了确保下载的 TS 切片多媒体数据能够正常播放、画面不失真，特制定本开发与下载说明。

------

## 💡 核心机制简述

CCTV 网页端为了防止非法下载，采用了基于 WebAssembly (WASM) 的加密混淆机制。

- **加密轨（不可用）**：特征 URL 中包含 `/asp/h5e/`。其 TS 切片过二进制混淆，直接下载后会导致**声音正常但图像花屏/绿幕失真**。
- **标准轨（可用）**：特征 URL 中包含 `/asp/hls/`。属于未混淆的标准 HLS 流，专供移动端或原生播放器，下载后**可直接无损播放与合并**。

本项目的核心思路是：**通过 VDN 鉴权接口，直接逆向获取标准轨（HLS）的真实未加密直链。**

------

## 🛠️ 下载与爬取核心步骤

### 1. 定位 VDN 核心接口并获取主 M3U8

首先，在视频播放页面通过抓包或自动化工具（如 Selenium/Playwright）拦截 CCTV 官方的视频分发网络（VDN）接口。

- **接口地址特征**：`[https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do?pid=](https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do?pid=)...`
- **核心操作**：解析该接口返回的 JSON 消息体，在数据中提取出**未加密的主 M3U8 地址**（通常位于 `hls_cdn_info` 字段）。

> 📌 **主 M3U8 示例**： `[https://hls.cntv.lxdns.com/asp/hls/main/0303000a/3/default/7f6fb5059e0c41d188ecf5f14113bf3e/main.m3u8?maxbr=2048](https://hls.cntv.lxdns.com/asp/hls/main/0303000a/3/default/7f6fb5059e0c41d188ecf5f14113bf3e/main.m3u8?maxbr=2048)`

------

### 2. 解析主 M3U8 获取目标码率的片段 M3U8

步骤 1 中获取的 `main.m3u8` 是一个主播放列表（Master Playlist），其中不包含具体的 `.ts` 视频片段地址，而是包含了不同清晰度/码率的子 M3U8 链接。

- **核心操作**：请求 `main.m3u8` 地址，根据项目所需的清晰度要求（如 450, 1200, 2000 等码率），组合或提取出**含有真实视频片段地址的二级 M3U8 响应**。

> 📌 **片段 M3U8 示例**（以 450 码率为例）： `[https://hls.cntv.lxdns.com/asp/hls/450/0303000a/3/default/7f6fb5059e0c41d188ecf5f14113bf3e/450.m3u8](https://hls.cntv.lxdns.com/asp/hls/450/0303000a/3/default/7f6fb5059e0c41d188ecf5f14113bf3e/450.m3u8)` *(请求该地址即可获得带有 0.ts, 1.ts, 2.ts 的标准 HLS 切片列表)*

------

## ⚠️ 避坑指南（红线）

在编写爬虫或过滤网络请求时，**请务必严格执行以下过滤规则**：

> ❌ **绝对不要使用含有 h5e 特征值的接口及链接！**
>
> 例如：`[https://dh5wswx02.v.cntv.cn/asp/h5e/hls/1200/.../8.ts](https://dh5wswx02.v.cntv.cn/asp/h5e/hls/1200/.../8.ts)`
>
> **原因**：带有 `h5e` (HTML5 Encrypted) 路径的视频流专门用于网页端 WASM 播放器。其视频帧的底层二进制数据被故意打乱。直接通过 Python 的 `requests` 或 `urllib` 盲拖该路径下的 TS 文件，由于缺少客户端的 WASM 解密算法支持，将彻底报废。
>
> 

> 

------

## 💻 对应 Python 代码实现逻辑伪代码




​    
    import requests
    
    import re
    
    def get_cctv_clean_m3u8(pid):
    
        # 1. 请求 VDN 接口
    
        vdn_url = f"https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do?pid={pid}&client=flash"
    
        res = requests.get(vdn_url).json()
    
    # 获取未加密的主 m3u8
    main_m3u8 = res.get("hls_cdn_info", {}).get("hls_url")
    
    # 安全检查：防御性过滤 h5e
    if "h5e" in main_m3u8:
        raise ValueError("安全警报：抓取到了加密的 h5e 轨道！")
        
    print(f"[成功] 提取到标准轨主M3U8: {main_m3u8}")
    
    # 2. 根据主 m3u8 逻辑，推导或请求其子码率列表 (如将 main.m3u8 替换为目标码率 450.m3u8)
    # 注：实际开发中可通过正则解析 main_m3u8 内容获取更准确的子路径
    target_m3u8 = main_m3u8.replace("/main.m3u8?maxbr=2048", "/450.m3u8")
    
    return target_m3u8