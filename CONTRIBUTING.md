# 贡献指南

## 分支与提交

- 从最新 `main` 创建短生命周期分支。
- 建议使用 `feature/`、`fix/`、`docs/`、`refactor/` 前缀。
- 提交信息使用 `类型: 简短说明`，例如 `fix: 修正 WLAN BSS 字段偏移`。
- 一个提交只处理一个逻辑问题；不要混入构建产物、真实用户数据或无关格式化。

## 开发流程

1. 安装开发依赖：`python -m pip install -r requirements-dev.txt`。
2. 修改代码并为缺陷补充回归测试。
3. 运行 `./scripts/check.ps1`。
4. 涉及打包、资源路径或动态导入时，运行 `./scripts/build.ps1`。
5. 提交 Pull Request，并填写验证、风险和回滚说明。

## 兼容性要求

- 保持 Windows 10/11 支持。
- 不得读取 Wi-Fi 明文密钥，不得未经用户确认修改无线配置。
- 用户数据结构发生变化时，应提供兼容读取或迁移逻辑。
- Native Wi-Fi 结构调整必须对照 `wlanapi.h`，并保留固定偏移回归测试。
- 新增外部依赖前说明离线交付、许可证、体积和安全影响。

## 发布检查

- `VERSION`、CHANGELOG、Git 标签三者一致。
- 单元测试、静态语法检查和 PyInstaller 构建通过。
- 新生成 EXE 能启动并通过 `/api/health`。
- 发布包中不包含本地 APPDATA、日志、截图、密钥或真实现场扫描数据。
