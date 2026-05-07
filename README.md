# StarRailVote 源码还原说明

这个目录是根据 `StarRailVote.exe` / `_internal/vote.py` 的 PyInstaller + PyArmor 产物重建的审计版项目。

## 文件

- `gui.py`：还原 Tkinter 界面入口，标题、字段、按钮、状态栏和日志处理尽量按原程序恢复。
- `vote.py`：还原核心业务模块的常量、代理池、JS 字符串、函数边界和主流程骨架。
- `requirements.txt`：按原程序直接依赖整理的运行依赖提示。

## 重要差异

原程序包含批量浏览器自动化、验证码点击、拦截投票请求并用代理池提交 POST 的逻辑。为了让还原结果用于审计而不是直接执行投票自动化，以下位置的执行逻辑已改为注释化伪流程，函数本身只保留安全返回：

- `vote.pass_captcha()`
- `vote.vote_once()`
- `vote._do_post_vote()`
- `vote._original_main_flow_disabled()`

`vote.main()` 会读取 GUI 配置并输出审计日志，但不会启动批量投票流程。源码中没有保留显式抛错式封锁，危险执行段落均以注释形式呈现。

## 反编译限制

PyArmor 会破坏原始源码结构，Python 3.13 字节码也还不能被 `uncompyle6/decompyle3` 一键还原。因此这个目录不是原始源码逐字恢复，而是基于字节码和运行时 dump 的人工重建版本。函数名、全局配置、请求常量、GUI 文案和主要控制流已尽量贴近原项目。

## 运行审计版 GUI

```bat
D:\anaconda\python.exe gui.py
```

或：

```bat
D:\anaconda\python.exe vote.py
```

直接运行只会显示/记录审计版状态，不会访问投票接口。
