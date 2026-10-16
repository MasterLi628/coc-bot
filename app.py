"""打包成 exe 的入口：不带参数打开控制面板；带 --bot 时在后台挂机（由控制面板启动）。"""
import sys

if __name__ == "__main__":
    if "--bot" in sys.argv:
        import bot
        bot.run()
    else:
        import panel
        panel.main()
