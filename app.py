"""程序入口（打包成 exe 也是它）：不带参数打开控制面板；带 --bot 时在后台挂机（由控制面板启动）。

源码运行：python app.py（控制面板）/ python app.py --bot（直接挂机）"""
import sys

if __name__ == "__main__":
    if "--bot" in sys.argv:
        from cocbot import bot
        bot.run()
    else:
        from cocbot import panel
        panel.main()
