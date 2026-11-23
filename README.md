# WirelessNetOptimizer

Windows 离线无线网络故障排查、空口扫描、热力图、现场勘测、漫游分析和 WLC 知识库工具。

应用仅监听 `127.0.0.1`，运行时不依赖外网；用户数据默认保存在 `%APPDATA%/WirelessNetOptimizer/`。

## 主要能力

- Windows Native Wi-Fi 空口扫描与 Beacon IE 能力解析
- RSSI、SNR、SINR、信道占用和干扰分析
- 图纸热力图、现场草图、实测打点及传播模型校准
- 漫游记录、无线历史趋势和本机只读诊断
- 离线 WLC 错误码、诊断剧本、命令和文献索引
- HTML、CSV、DOCX 报告及项目备份恢复

## 环境要求

- Windows 10/11
- Python 3.11 或更高版本
- Node.js 仅用于 JavaScript 语法检查
- PyInstaller 仅用于生成单文件 EXE

运行时使用 Python 标准库，不需要第三方网络服务。

## 本地运行

```powershell
python wireless_app/app.py --no-browser
```

随后访问终端显示的 `http://127.0.0.1:<port>/`。

## 开发与验证

```powershell
python -m pip install -r requirements-dev.txt
./scripts/check.ps1
```

质量检查包括：Python 单元测试、全部 Python/spec 语法解析、前端 JavaScript 语法检查和 Git 空白错误检查。

## 构建

```powershell
./scripts/build.ps1
```

产物位于 `dist/WirelessNetOptimizer.exe`。`dist/`、`build/` 和发布 ZIP 不应提交到源码仓库；正式二进制应通过 GitHub Releases 或 CI Artifact 分发。

生成 Windows 安装程序（需要 Inno Setup 6）：

```powershell
./scripts/build-installer.ps1
```

安装包输出为 `dist/WirelessNetOptimizer-Setup.exe`，默认按当前用户安装，包含开始菜单、可选桌面快捷方式和卸载入口。卸载时会保留 `%APPDATA%/WirelessNetOptimizer` 中的项目与勘测数据。

## 目录结构

```text
wireless_app/        应用后端、扫描模块、前端与离线数据
tests/               回归测试
scripts/             本地质量检查和构建入口
installer/           Inno Setup 安装程序定义
.github/workflows/   Windows CI 与打包工作流
vendor_docs/         知识库生成所需的来源快照
build_*.py           离线数据生成脚本
```

## 版本管理

应用版本的唯一来源是 `wireless_app/version.py`。发布时应同时：

1. 更新 `wireless_app/version.py`；
2. 把变更从 `CHANGELOG.md` 的 `Unreleased` 移入对应版本；
3. 确认 CI 和 Windows EXE 冒烟测试通过；
4. 创建带 `v` 前缀的 Git 标签并在 GitHub Releases 上传二进制。

贡献流程参见 [CONTRIBUTING.md](CONTRIBUTING.md)，安全问题参见 [SECURITY.md](SECURITY.md)。

## 许可

仓库目前没有声明开源许可证。在维护者明确许可证前，请不要假定代码可以被复制、再分发或商用。
