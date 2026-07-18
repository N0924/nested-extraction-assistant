"""嵌套解压助手的本地桌面入口。"""

from packaged_runtime import prepare_packaged_tk


prepare_packaged_tk()

from ui.main_window import run_application


if __name__ == "__main__":
    run_application()
