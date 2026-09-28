# 注意打开全局代理才可以使用
# 有问题首先考虑   pip install --upgrade yt-dlp
import time

import yt_dlp

def download_one(url):
    ydl_opts = {
        #'proxy': 'http://127.0.0.1:7890',
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        # 'username': 'marsjjp@gmail.com',
        # 'password': 'mars750101',
    }

    while True:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            break
        except Exception as e:
            print(e)
            time.sleep(5)


if __name__ == "__main__":

    url1 = "https://www.youtube.com/watch?v=v7T1o1b6QmY"     #

    for url in (url1, ):
        download_one(url)

