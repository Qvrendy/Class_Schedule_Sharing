# 课表共享网站（Class Schedule Sharing）

一个基于 Flask 和 SQLite 的课程表管理与分享网页。支持两位用户分别维护课表，并通过专属链接进行编辑或只读分享。

## 功能简介
- **周课表展示**：按星期和节次展示课程，支持 11 个节次及跨节课程。
- **多人课表隔离**：内置「我」和「同学A」两个用户，课程按用户分别保存。
- **链接分享**：
  - `/?u=<分享码>`：专属访问链接，可编辑对应用户自己的课表。
  - `/view/<分享码>`：只读课表链接，适合分享给他人查看。
- **课程管理**：手动添加、文字批量添加、编辑和删除课程，也可以清空自己的课表。
- **AI 辅助录入**：可调用 DeepSeek 从文字中提取课程信息；可调用智谱 GLM 识别课表截图。识别结果需要用户确认后才保存。
- **个性化设置**：支持修改昵称和上传头像。

## 技术栈
- Python、Flask、SQLite（Python 内置 sqlite3）
- HTML、CSS、JavaScript
- requests、python-dotenv
- DeepSeek API（文字课程提取，需配置 API Key）
- 智谱 GLM API（课表截图识别，需配置 API Key）

## 本地运行
建议使用 Python 3.10 或更高版本。

### 1. 获取项目
    git clone https://github.com/Qvrendy/Class_Schedule_Sharing.git
    cd Class_Schedule_Sharing

### 2. 创建并启用虚拟环境（推荐）
Windows PowerShell：
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1

macOS / Linux：
    python3 -m venv .venv
    source .venv/bin/activate

### 3. 安装依赖
    python -m pip install -r requirements.txt

### 4. 启动应用
    python app.py

然后访问 http://127.0.0.1:5000/ 。首次启动时，程序会在项目目录中自动创建 SQLite 数据库 schedule.db，并初始化用户和示例课程。

## AI 功能配置（可选）
不配置 API Key 时，网站的基础课表功能仍可使用；对应的 AI 录入功能则无法正常调用模型。

可以在项目根目录创建本地 .env 文件，按需填写：
    DEEPSEEK_API_KEY=你的_DEEPSEEK_API_Key
    GLM_API_KEY=你的_GLM_API_Key

也可以按需配置接口地址、模型名称和学期开学日期：
    DEEPSEEK_API_URL=https://api.deepseek.com/chat/completions
    DEEPSEEK_MODEL=deepseek-chat
    GLM_API_URL=https://open.bigmodel.cn/api/paas/v4/chat/completions
    GLM_MODEL=glm-4v-flash
    SEMESTER_START_DATE=2026-09-01

SEMESTER_START_DATE 表示第 1 周星期一的日期，格式为 YYYY-MM-DD。具体可配置项以 app.py 中的环境变量读取逻辑为准。

## 数据与安全注意事项
- schedule.db 保存用户、分享码和课程数据，是运行数据，不是项目源码。
- .gitignore 已忽略 SQLite 数据库及备份、.env、.secret_key、Python 缓存、虚拟环境和运行时头像目录。
- **不要将 API Key、密码、Token、.env、.secret_key 或含个人数据的数据库文件提交到 GitHub。**
- 如需迁移或备份实际课表数据，请单独、安全地备份数据库；不要把真实用户数据当作公开项目示例。
- 专属链接中包含分享码。请只把可编辑链接发给需要编辑该用户课表的人；公开分享时优先使用只读链接。

## 部署说明
项目提供了通用部署说明：[DEPLOY.md](DEPLOY.md)。应用入口为 app.py，依赖列表见 requirements.txt。部署到云平台时，应在平台环境变量中配置所需密钥，并确保 SQLite 数据库位于可持久化、可写入的位置。

## 项目状态
这是一个个人课表管理与分享项目，目前以代码记录、备份和学习参考为主。后续是否继续维护，取决于实际需要。

## License
目前仓库尚未声明开源许可证。若未来将仓库设为 Public，建议先选择合适的许可证；添加许可证之前，其他人不应默认拥有复制、修改或再发布代码的授权。
