# VicroCode 部署指南（极简版）

## 1. 打包上传哪些文件

**必须上传（共 4 项）**

```
app.py                  ← 程序入口，平台启动命令：python app.py
requirements.txt        ← 依赖：Flask / requests / python-dotenv
templates/              ← index.html、share.html
static/                 ← style.css、ui.js
```

**可选**

| 文件 | 什么时候传 |
| --- | --- |
| `schedule.db` | 想保留本地已有的课表和「分享码」就一起传；不传则云端首次启动自动建库并生成示例课程（分享码会重新随机生成） |

**一条命令打包（Windows PowerShell，在项目根目录执行）**

```powershell
Compress-Archive -Path app.py,requirements.txt,templates,static -DestinationPath class-schedule.zip -Force
# 想连课表数据一起传，就把 schedule.db 也加进 -Path
```

这样 `.env`、`.secret_key`、`__pycache__`、`.vscode` 都不会被打进压缩包，天然不会传错。


## 2. 千万不要上传

| 文件 / 目录 | 原因 |
| --- | --- |
| `.env` | 里面是 API Key，传到云端等于公开密钥；云端改用「环境变量」配置 |
| `.secret_key` | 程序自动生成的 session 密钥，云端会自己生成一个 |
| `__pycache__/`、`*.pyc` | Python 缓存，平台会自己生成，传了还可能引起版本不匹配 |
| `.vscode/` | 只是本地编辑器配置 |
| `venv/`、`.venv/`、`.git/` | 环境/历史记录，不需要也不应该传 |

## 3. 平台侧配置

1. **入口文件 / 启动命令**：`app.py`，启动命令 `python app.py`
   （程序已从环境变量 `PORT` 读取端口、监听 `0.0.0.0`，不用手写端口；`debug` 已写死 `False`）
2. **依赖安装**：确认平台已按 `requirements.txt` 安装依赖（有「安装依赖」按钮就点一下）
3. **环境变量**（平台控制台的「环境变量」里逐条添加）：

| 变量名 | 必填 | 说明 |
| --- | --- | --- |
| `SECRET_KEY` | ✅ | session 签名密钥。生成方法：`python -c "import secrets;print(secrets.token_hex(32))"`，填结果即可 |
| `DEEPSEEK_API_KEY` | ✅ | 文字批量添加用 |
| `GLM_API_KEY` | ✅ | 课表截图识别用（智谱） |
| `DEEPSEEK_API_URL`、`DEEPSEEK_MODEL` | 可选 | 有默认值，不填就是官方接口 / `deepseek-chat` |
| `GLM_API_URL`、`GLM_MODEL` | 可选 | 有默认值，不填就是官方接口 / `glm-4v-flash` |
| `DB_PATH` | 可选 | 只有平台要求把数据放到别的可写目录时才用，例如 `/data/schedule.db`；不填就是 `app.py` 同级的 `schedule.db` |

> 没有 `.env` 文件完全不影响启动：程序只会在 `.env` 存在时读取它，其余配置全部走 `os.getenv()`。

## 4. 部署后自检（1 分钟）

1. 打开 `https://<平台分配的域名>/` → 能看到周课表页面即成功。
2. 页面顶部会显示「专属免密链接」和「只读链接」：
   - `/?u=<分享码>`：打开后以这个身份**编辑**自己的课表
   - `/view/<分享码>`：**只读**分享给同学
3. 试一次「文字添加」→ 验证 `DEEPSEEK_API_KEY`；试一次「截图识别」→ 验证 `GLM_API_KEY`。
   没配 Key 不会导致网站打不开，只会在提交时提示缺少 Key。
   - 顺手再点一次课程卡片上的 ✏️ 改一门课、点「清空我的课表」清空、点「修改昵称」改个名字：
     这三处操作都只影响**你自己**的课表，同学的课一门都不会变。
4. 如果复制出来的链接是 `http://` 开头而平台是 HTTPS，把开头的 `http` 手动改成 `https` 再发即可。

## 5. 常见坑

- **端口**：不要写死 5000，云端用 `$PORT`；本程序已处理。
- **debug**：上线必须关，本程序已写死 `debug=False`（开着会暴露源码和调试器）。
- **数据丢失**：`schedule.db` 是单文件 SQLite，必须放在**可持久化**的目录；如果平台每次部署都会重置项目目录，用 `DB_PATH` 指到平台的数据目录，或者重新上传 `schedule.db`。
- **截图大小**：代码限制单张 5MB，平台可能限制更小，太大就先压缩。
- **分享码变了**：重新上传不带 `schedule.db` 的项目会重新生成用户和分享码，记得把新链接重新发给同学。
