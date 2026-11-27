"""课表展示网页 —— Flask + sqlite3 + DeepSeek 自然语言录入

运行方式（在项目根目录打开终端）：
    python app.py
然后浏览器访问： http://127.0.0.1:5000
云端部署：平台执行同一条命令即可（python app.py），
         监听端口从环境变量 PORT 读取（本地没有 PORT 时用 5000），debug 固定为 False。

身份识别（免密链接，没有下拉框了）：
    http://<域名>/?u=<share_code>    专属免密链接：打开就把 session 绑成这个用户，只能改他自己的课表
    http://<域名>/view/<share_code>  只读链接：只显示课表，页面里没有任何表单；
                                    打开它**绝不会**改 session 里的身份（不读 ?u=、不写 session），
                                    页面里的「回首页」也只会带访问者自己的 share_code，不会出现裸链接 /
    http://<域名>/                    不带参数：沿用 session 里已记住的身份；
                                     session 里也没有 → 显示「请用 ?u=你的专属码 访问」的提示页（HTTP 403），
                                     绝不默认绑成第一个用户「我」
    安全约定：写操作（上传截图 / 文字批量添加 / 手动添加 / 修改 / 删除 / 一键清空 / 改昵称 / 换头像）
             只认 session，从不读取 URL 或表单里的 u / user_id / share_code，
             改参数改不了别人的课表；session 里没有身份时，写操作一律 403，
             不会被默认绑成「我」。

头像：
    首页身份栏的小头像点一下就能换（POST /upload-avatar）。
    图片限制 2MB 以内，只收 JPG / PNG / GIF / WebP，
    文件存到 static/avatars/ 目录，users.avatar 里只记「相对 static 的路径」
    （例如 avatars/1_a1b2c3.jpg），页面用 url_for('static', ...) 显示。

配置：
    本地开发：项目根目录的 .env 里放 API Key（这个文件不要上传到代码仓库）。
    云端部署：没有 .env 也能正常启动，配置写在平台的「环境变量」里：
        PORT            平台自动注入，程序启动时读取
        SECRET_KEY      session 签名密钥（云端务必设置，随便一串长随机字符）
        DEEPSEEK_API_KEY / GLM_API_KEY
        DEEPSEEK_API_URL / DEEPSEEK_MODEL / GLM_API_URL / GLM_MODEL（可选，有默认值）
        SEMESTER_START_DATE
                        学期开学日期（第 1 周的星期一），格式 YYYY-MM-DD，默认 2026-09-01。
                        首页 / 只读页打开时按「今天 - 开学日期」自动定位到当前周。

数据库：
    项目根目录下的 schedule.db（SQLite 文件），程序启动时会自动创建。
    表结构：
        users   —— 用户（id, username, share_code, avatar），初始化时自动建「我」「同学A」
                   avatar 是头像相对 static 的路径（avatars/xxx.jpg），没上传过就是空字符串
        courses —— 课程，靠 user_id 区分属于谁
    想直接看里面的数据，可以用命令：
        sqlite3 schedule.db "SELECT * FROM users;"
        sqlite3 schedule.db "SELECT * FROM courses;"

数据库重置：
    本程序启动时会检查 courses 表有没有 user_id 字段，
    老版本（单人版）的数据库会被自动清空重建，所以升级到双人版不需要手动删库。
    想彻底重来（比如想让示例课程重新出现），关掉程序后删掉 schedule.db 再运行：
        Remove-Item schedule.db
        python app.py
"""

import base64
import json
import os
import re
import secrets
import sqlite3
import string
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

# ===== 路径 =====
# BASE_DIR = app.py 所在的目录。下面所有文件都按它来找，
# 所以本地在项目根目录启动、云端在别的目录启动，都不会找错文件。
BASE_DIR = Path(__file__).resolve().parent

# 把 .env 里的配置读进环境变量，之后用 os.getenv() 就能取到。
# .env 只用于本地开发；云端没有这个文件也很正常，is_file() 判断一下就跳过，
# 不会抛异常（云端配置全部写在平台的「环境变量」里，见文件顶部说明）。
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.is_file():
    load_dotenv(ENV_PATH)

# 数据库文件默认就是 app.py 同级的 schedule.db（相对项目根目录的路径，
# 不受启动时工作目录影响）。云端如果把可写目录放在别处，可以用环境变量 DB_PATH 覆盖：
#     绝对路径 -> 原样使用；相对路径 -> 仍然按 BASE_DIR 解析
_db_path = os.getenv("DB_PATH", "").strip()
DB_PATH = Path(_db_path).expanduser() if _db_path else BASE_DIR / "schedule.db"
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH
# 自定义 DB_PATH 指向的子目录可能还不存在，先建好，
# 否则 sqlite3.connect() 会报 unable to open database file。
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# DeepSeek 的接口地址和模型。写在 .env 里可以不改代码就切换
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
REQUEST_TIMEOUT = 30  # 秒，网络不好时别让页面一直转圈

# 智谱 GLM 视觉模型配置（同样从 .env 读取）
GLM_API_URL = os.getenv(
    "GLM_API_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions"
)
GLM_MODEL = os.getenv("GLM_MODEL", "glm-4v-flash")
IMAGE_TIMEOUT = 60  # 秒，视觉模型识别课表截图通常要十几秒，给足时间
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 智谱对 base64 图片大小有上限，先按 5MB 原始文件挡一下
# 头像上传限制：超过这个大小直接拒绝（前端也会先挡一次，这里才是真正的兜底）
MAX_AVATAR_BYTES = 2 * 1024 * 1024  # 2MB

# 头像存放目录：static/avatars/。放在 static 下面，Flask 就能直接把它当静态文件返回，
# 不用再写一个「读文件 + 设置 Content-Type」的路由。
# users.avatar 里存的是它下面的文件名前缀路径，例如 "avatars/1_a1b2c3.jpg"。
AVATAR_DIR = BASE_DIR / "static" / "avatars"
AVATAR_URL_PREFIX = "avatars"

# 目录不存在就先建好（第一次运行、或者把项目拷到新机器上都能直接上传头像）。
# 目录不可写（例如只读的云端文件系统）时只记一条警告，不影响程序启动。
try:
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
except OSError as exc:
    print(f"[警告] 无法创建头像目录 {AVATAR_DIR}：{exc}（换头像时会再试一次）")

# 分享码：6 位小写字母 + 数字，例如 x8f2a。
# 每个用户在 users 表里各有一个 share_code，第一次初始化时随机生成，
# 之后一直复用它，所以「我的课表」只读链接 http://127.0.0.1:5000/view/x8f2a 是固定的。
SHARE_CODE_LENGTH = 6
SHARE_CODE_ALPHABET = string.ascii_lowercase + string.digits

# 修改课程 / 改昵称时的长度上限：只做基本防呆，避免超长文本把页面撑坏。
# （新增的 /edit、/update-name 两个写操作会用它们校验，超长直接给出人话提示）
MAX_COURSE_NAME_LENGTH = 100
MAX_LOCATION_LENGTH = 200
MAX_USERNAME_LENGTH = 20

# ===== 双人共享 =====
# 初始化时自动创建这两个固定用户。
# 想改名 / 加人：直接改这个元组，然后删掉 schedule.db 重新运行程序。
FIXED_USERS = ("我", "同学A")

# 每个用户「第一次建出来」时塞进去的示例课程，方便一打开页面就有内容看。
# 格式：(课程名, 星期几, 开始节次, 结束节次, 地点, 周数)
DEMO_COURSES = {
    "我": [
        ("高等数学", 1, 1, 2, "教学楼 A101", "1-16"),
        ("大学英语", 3, 3, 4, "外语楼 B203", "1-8"),
    ],
    "同学A": [
        ("线性代数", 2, 3, 4, "教学楼 B202", "1-16"),
        ("程序设计", 4, 5, 6, "机房 C301", "1-16"),
    ],
}

# 下标 + 1 正好对应数据库里的 weekday（1 = 星期一 ... 7 = 星期日）
WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

# ===== 课表节次（教务系统样式）=====
# 教务系统固定排 11 节：上午 1~4、下午 5~8、晚上 9~11，第 4 节后是「中午」。
# 数据库里 period 表示开始节次，end_period 表示结束节次；
# 前端用 CSS Grid 渲染，课程从 period 跨行到 end_period。
NUM_PERIODS = 11
LUNCH_AFTER_PERIOD = 4          # 第 4 节和第 5 节之间插入「中午」分割行
LUNCH_ROW = LUNCH_AFTER_PERIOD + 2   # 表头占第 1 行，所以中午是第 6 行

# 左侧时间轴的时间字典。改这里就能换成本校的作息时间。
PERIOD_TIMES = {
    1: ("08:00", "08:45"),
    2: ("08:55", "09:40"),
    3: ("09:50", "10:35"),
    4: ("10:45", "11:30"),
    5: ("14:00", "14:45"),
    6: ("14:55", "15:40"),
    7: ("15:50", "16:35"),
    8: ("16:45", "17:30"),
    9: ("19:00", "19:45"),
    10: ("19:55", "20:40"),
    11: ("20:50", "21:35"),
}

# 模板里直接循环 period_rows 渲染左侧时间轴和空白格子。
# grid_row 是 CSS Grid 的行号：第 1 行是表头，第 4 节之后多出一行「中午」。
PERIOD_ROWS = [
    {
        "number": period,
        "start": PERIOD_TIMES[period][0],
        "end": PERIOD_TIMES[period][1],
        "grid_row": period + 1 + (1 if period > LUNCH_AFTER_PERIOD else 0),
    }
    for period in range(1, NUM_PERIODS + 1)
]

# ===== 上课周数（weeks 字段）=====
# 数据库里存成一段文本，由 normalize_weeks() 统一成下面这几种写法：
#     "1-16"       第 1~16 周都上
#     "1-16单"     第 1~16 周里的单周
#     "2-16双"     第 2~16 周里的双周
#     "1-8,10-16"  多段周数
#     "3"          只有第 3 周
# 前端 index.html / share.html 里的 expandWeeks() 按同一套规则解析，两边保持一致。
DEFAULT_WEEKS = "1-16"   # 原文没提到周数时用它（需求要求老数据也填这个）
DEFAULT_WEEK_END = 16    # 只说「单周」而没给范围时，默认到第 16 周为止
MAX_WEEK = 20            # 周数选择器 / 前端过滤的最大周数

# ===== 自动定位当前周 =====
# 学期开学日期（第 1 周的星期一）。程序按「今天 - 开学日期」算出今天是第几周：
#     第几周 = (今天 - 开学日期).days // 7 + 1，再限制在 1 ~ MAX_WEEK 之间。
# 换学期只改这一行；也可以不改代码，在 .env / 平台环境变量里设置：
#     SEMESTER_START_DATE=2027-02-22
SEMESTER_START_DATE = os.getenv("SEMESTER_START_DATE", "2026-09-01").strip()

# 全角数字转半角（用户可能输入「１－８周」）
FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

# AI 有时不给 1~7，而是给「周三」「Wednesday」，这里做一张对照表来兜底
WEEKDAY_WORDS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "日": 7,
    "天": 7,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
    "sun": 7,
}

# 「第二节」这种中文数字的兜底对照（十以上单独处理）
CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

# 发给 DeepSeek 的“系统提示词”：告诉模型要输出什么格式、怎么理解“下午第二节”“第1-8周”。
# 文字添加现在支持一次粘贴多门课，所以要求模型返回 JSON 数组（每门课一个对象）。
AI_SYSTEM_PROMPT = """你是一个课表信息提取助手。用户会给出一段文字，里面可能有一门或多门课程（通常一行一门）。你要提取文字里的所有课程，返回一个 JSON 数组。

只输出一个 JSON 数组，不要输出任何解释，也不要用 Markdown 代码块。数组里每个对象的字段固定为：
[
  {
    "name": "课程名",
    "weekday": 1,
    "period": 1,
    "end_period": 2,
    "location": "上课地点，没提到就填空字符串",
    "weeks": "1-16"
  }
]

规则：
1. weekday 是 1~7 的整数：1=星期一、2=星期二、3=星期三、4=星期四、5=星期五、6=星期六、7=星期日。
   “周三”“星期三”“礼拜三”都要转成 3。
2. period 是开始节次，end_period 是结束节次，取值都是 1~11，按“上午 4 节、下午 4 节、晚上 3 节”的顺序编号：
   上午第 1~4 节 = 1~4；下午第 1~4 节 = 5~8；晚上第 1~3 节 = 9~11。
   例如“下午第二节课” = 6，“上午第一节” = 1。
   如果用户只说“第 3 节”而没说上午/下午/晚上，就按字面取 3。
2.1 连堂课必须取连续区间：
   - “1-2节”“第1、2节” -> period = 1, end_period = 2
   - “第3,4节”“3至4节” -> period = 3, end_period = 4
   - “5-6节” -> period = 5, end_period = 6
   - 只写单节（例如“第3节”）-> period = 3, end_period = 3
   end_period 必须大于或等于 period，且不能超过 11。
3. name 只保留课程名本身，例如“在图书馆上英语” -> "英语"，不要写成“英语课”。
4. location 尽量保留原文，例如“图书馆”“教学楼 A101”。
5. 如果确实无法判断 weekday 或 period，就填 0，表示没解析出来（不要瞎猜）；无法判断 end_period 时让它等于 period。
6. weeks 是上课周数，必须是字符串，统一写成下面的格式（不要带“周”字，不要输出“每周”“全周”这类词）：
   - 连续周写成“起-止”：「1-8周」-> "1-8"；「第 1 到 16 周」-> "1-16"
   - 单周（隔周上课，第 1、3、5… 周）写成“起-止单”：「1-16周单周」-> "1-16单"；只说「单周」-> "1-16单"
   - 双周（第 2、4、6… 周）写成“起-止双”：「2-16周双周」-> "2-16双"；只说「双周」-> "2-16双"
   - 多段周数用英文逗号连接：「1-8周和10-16周」-> "1-8,10-16"；「1、3、5、7周」-> "1,3,5,7"
   - 只有一周时直接写数字：「第3周」-> "3"
   - 周数最大到第 20 周，超过 20 的按 20 截断
   - 如果原文完全没提到周数，必须返回 "1-16"，不要留空
7. 原文里有几门课就返回几个对象，顺序保持和原文一致；只有一门课时，也要返回“只包含一个对象”的数组。
8. 不要把多门课合并成一个对象，也不要漏掉任何一门课。
"""

# 识别课表截图用的系统提示词（字段和文字解析保持一致，也要返回连堂区间）
IMAGE_SYSTEM_PROMPT = (
    "你是一个课表识别助手。请提取图片中的课表信息，以 JSON 列表格式返回，"
    "包含 name(课程名), weekday(1-7), period(开始节次 1-11), "
    "end_period(结束节次 1-11), location(地点), weeks(上课周数) 六个字段。"
    "如果课程占连续两节或更多节（例如 1-2 节），period 填开始节次、"
    "end_period 填结束节次；只占一节时 end_period 等于 period。"
    "weeks 必须是字符串：连续周写 1-16，单周写 1-16单，双周写 2-16双，"
    "多段用英文逗号连接（例如 1-8,10-16）；图片里没写周数就填 1-16。"
    "不要输出任何多余的文字。"
)

# 创建 Flask 应用对象
# __name__ 让 Flask 知道去哪里找 templates 文件夹
app = Flask(__name__)


def load_secret_key():
    """session 签名密钥：优先环境变量 SECRET_KEY，其次本地 .secret_key 文件，最后随机生成。

    密钥一变，浏览器里记住的身份就失效（会退回第一个用户「我」），不影响课表数据。
    - 云端：请在平台的「环境变量」里设置 SECRET_KEY，重新部署也不会让已绑定的身份掉线；
    - 没设置时：第一次启动自动生成到项目根目录的 .secret_key 并一直复用。
      不再用写死的字符串当密钥 —— 那样别人拿到源码就能伪造 Cookie 冒充别人。
    """
    key = os.getenv("SECRET_KEY", "").strip()
    if key:
        return key

    key_file = BASE_DIR / ".secret_key"
    try:
        if key_file.is_file():
            key = key_file.read_text(encoding="utf-8").strip()
            if key:
                return key
        key = secrets.token_hex(32)
        key_file.write_text(key, encoding="utf-8")
        print(f"[提示] 未设置 SECRET_KEY，已自动生成并保存到 {key_file}（这个文件不要上传）")
        return key
    except OSError:
        # 目录不可写（例如只读的云端文件系统）：本次进程临时用随机密钥，不影响启动
        print("[警告] 无法写入 .secret_key，本次使用临时 SECRET_KEY，重启后需要重新打开专属链接")
        return secrets.token_hex(32)


# session（用来记住「当前操作人是谁」）需要这把密钥来签名 Cookie
app.secret_key = load_secret_key()

# 免密链接打开的页面会把身份记久一点（180 天）：
# 这样下次直接输域名进来，还是原来那个人，不会被默认绑成「我」。
app.permanent_session_lifetime = timedelta(days=180)

# 写操作只认 session 里的身份。请求里出现这些字段一律拒绝，
# 防止有人拿 /add?u=别人的分享码 或表单里的隐藏 user_id 去改别人的课表。
# 注意：因为 username 也在这张名单里，「修改昵称」的 /update-name 表单字段
#       特意叫 nickname，而不是 username（见 update_name()）。
IDENTITY_PARAM_KEYS = ("u", "user_id", "share_code", "username")

# 只读页：/view/<code>（以及兼容老地址的 /share/<code>）。
# 这两个页面只允许「看」：处理期间绝不能修改 session 里的 user_id，
# 否则同学点开只读链接就可能被悄悄绑成 owner，白拿编辑权限。
READONLY_ENDPOINTS = ("view_schedule", "share_schedule")


@app.before_request
def reject_identity_override_on_writes():
    """写操作只认 session：URL 参数或表单里塞 u / user_id / share_code 一律 400。

    这是「不能通过改 URL 参数越权修改别人课表」的兜底：
    就算有人手动构造 POST /add?u=别人的分享码，或者往表单里藏一个 user_id，
    也进不了业务逻辑，不会被写进别人的课表。
    """
    if request.method != "POST":
        return None
    if any(key in request.values for key in IDENTITY_PARAM_KEYS):
        abort(
            400,
            "写操作只认当前 session 里的身份，不接受 URL 或表单里的身份参数。"
            "请用你自己的专属免密链接（/?u=你的分享码）打开首页后再修改。",
        )
    return None


@app.before_request
def require_identity_for_writes():
    """写操作（POST）必须已经有 session 身份，没有就 403。

    以前 session 为空时会默认绑成「我」，于是匿名 POST /add 也能往「我」的课表里写；
    现在身份只能从专属免密链接（GET /?u=...）获得，所以这里直接挡掉匿名写请求，
    连 AI 接口都不会被调用。
    """
    if request.method != "POST":
        return None
    if peek_current_user() is None:
        abort(
            403,
            "还没有身份：请用你自己的专属免密链接（/?u=你的分享码）"
            "打开首页后再修改课表。",
        )
    return None


@app.before_request
def snapshot_identity_on_readonly_pages():
    """只读页开始处理前，先把 session 里的身份**只读地**记在 g 上（不写 session）。

    记下来只为了在 after_request 里核对：只读页从头到尾都不许换身份。
    """
    if request.endpoint in READONLY_ENDPOINTS:
        g.readonly_identity_before = session.get("user_id")
    return None


@app.after_request
def restore_identity_on_readonly_pages(response):
    """兜底校验：只读页返回前，如果 session 里的 user_id 被改过，立刻还原。

    正常路径根本不会改 —— render_share() 只用 peek_current_user()（只读 session），
    不调用 get_current_user() / bind_user_by_share_code()。
    这一层是防止以后改代码时不小心在只读页里写了 session：
    只要发生，就把身份改回访问前的值并记一条日志，绝不让「看别人课表」
    变成「拿到别人（或「我」）的编辑权限」。
    """
    if request.endpoint not in READONLY_ENDPOINTS:
        return response
    if not hasattr(g, "readonly_identity_before"):
        return response

    before = g.readonly_identity_before
    if session.get("user_id") != before:
        app.logger.warning(
            "只读页试图修改 session 身份，已拒绝并还原：%s", request.path
        )
        if before is None:
            # 访问前本来没有身份 → 把偷加上的身份删掉
            session.pop("user_id", None)
        else:
            session["user_id"] = before
    return response


class AIError(Exception):
    """和 AI 相关的错误：调用失败、返回格式不对、解析结果不合理。

    这类错误的文字会直接显示在网页上给用户看，所以文案要写得人话一点。
    """


def get_db():
    """打开数据库连接。

    row_factory = sqlite3.Row 之后，查询结果可以像字典一样用列名取值：
        row["name"]、row["weekday"] ...
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # 打开外键约束：这样 courses.user_id 必须真的存在于 users 表里
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def generate_share_code():
    """随机生成一个分享码：6 位小写字母 + 数字，例如 x8f2a。"""
    return "".join(secrets.choice(SHARE_CODE_ALPHABET) for _ in range(SHARE_CODE_LENGTH))


def unique_share_code(conn):
    """生成一个当前库里还没被占用的分享码（两个用户不能撞码）。"""
    while True:
        code = generate_share_code()
        taken = conn.execute(
            "SELECT 1 FROM users WHERE share_code = ?", (code,)
        ).fetchone()
        if not taken:
            return code


def get_users():
    """所有用户，按 id 排序（第一个就是「没带参数时的默认身份」）。"""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, username, share_code, avatar FROM users ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


def get_user(user_id):
    """按 id 查一个用户；查不到返回 None。

    身份现在只由专属免密链接（share_code）决定，这个函数留给调试 / 以后扩展用。
    """
    conn = get_db()
    row = conn.execute(
        "SELECT id, username, share_code, avatar FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row


def get_user_by_share_code(share_code):
    """按分享码查用户（串门 / 只读页用）；查不到返回 None。"""
    conn = get_db()
    row = conn.execute(
        "SELECT id, username, share_code, avatar FROM users WHERE share_code = ?",
        (share_code,),
    ).fetchone()
    conn.close()
    return row


def peek_current_user():
    """**只读地**看一眼 session 里已经绑定的用户；没绑定 / 绑的人已经不在了就返回 None。

    和 get_current_user() 的区别：这里绝不写 session、也绝不自动退回第一个用户。
    只读页 /view/<share_code> 用它来判断「你是谁」——否则同学一打开只读链接，
    就会被悄悄当成「我」，反而拿到了「我」的编辑身份。
    """
    current_id = session.get("user_id")
    if current_id is None:
        return None

    conn = get_db()
    row = conn.execute(
        "SELECT id, username, share_code, avatar FROM users WHERE id = ?", (current_id,)
    ).fetchone()
    conn.close()
    return row


def bind_user_by_share_code(share_code):
    """把专属免密链接里的 share_code 绑进 session。

    这就是「免密」的全部秘密：链接本身就是凭证（谁拿到链接，就等于拿到了这个身份）。
    找到用户 → 改写 session 里的身份并返回他；
    找不到   → 返回 None，而且**不动** session，避免一个错链接把已绑定的身份换掉。
    """
    user = get_user_by_share_code(share_code)
    if user is None:
        return None

    session.permanent = True  # 用长效 Cookie，下次直接输域名进来还是这个人
    session["user_id"] = user["id"]
    return user


def get_current_user():
    """当前操作人：只看 session（浏览器 Cookie），不接受请求里的任何参数。

    - session 里的 user_id 还有效：直接用
    - 没有 / 已失效（例如删库重建过）：直接 403，**不再**默认绑成第一个用户「我」
    身份只有一个合法来源：用专属免密链接 /?u=<share_code> 打开首页（见 index 路由）。
    所有写操作都走这里，所以匿名请求（session 里没身份）永远改不了任何人的课表。
    """
    user = peek_current_user()
    if user is None:
        abort(
            403,
            "还没有身份：请用你自己的专属免密链接（/?u=你的分享码）"
            "打开首页后再操作。",
        )
    return user


def reset_outdated_schema(conn):
    """旧版数据库（courses 表没有 user_id）直接清空重建。

    目前库里都是测试数据，没必要写复杂的平滑迁移：
    一发现结构不对，就把旧的 courses / settings 表删掉，后面按新结构重新建。
    """
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "courses" not in tables:
        return  # 全新的数据库，什么也不用做

    columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(courses)").fetchall()
    }
    if "user_id" in columns:
        return  # 已经是双人版结构

    conn.execute("DROP TABLE courses")
    if "settings" in tables:
        conn.execute("DROP TABLE settings")  # 旧版存全局 share_code 的表，已经用不上了
    conn.commit()
    print(
        "检测到旧版数据库结构（courses 表没有 user_id 字段），已清空重建。"
        "旧的测试数据不再保留。"
    )


def migrate_users_avatar(conn):
    """给老数据库的 users 表补上 avatar 字段（新库建表时已经带了，这里会直接返回）。

    为什么不用删库重建：头像只是新增的一列，老数据（昵称 / 分享码 / 课程）都还能用，
    用 ALTER TABLE ADD COLUMN 补一列最省事，也不需要用户重新打开专属链接。
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "avatar" in columns:
        return  # 新库 / 已经升级过

    conn.execute("ALTER TABLE users ADD COLUMN avatar TEXT NOT NULL DEFAULT ''")
    conn.commit()
    print("检测到 users 表没有 avatar 字段，已自动添加（已有数据保持不变）")


def migrate_courses_end_period(conn):
    """平滑升级 courses 表：新增 end_period（结束节次），不删任何已有课程。

    升级步骤：
        1) 老库没有 end_period -> ALTER TABLE ADD COLUMN 补一列；
        2) 老课程的结束节次默认等于开始节次（原来一格一门课，都是单节）；
        3) 顺手修复异常值：end_period < period、NULL、0，以及旧版本允许的第 12 节
           （新 UI 固定 11 节，超过 11 的截到 11，课程本身不会丢）。
    整个过程只做 ADD COLUMN / UPDATE，没有 DROP TABLE，重启多少次结果都一样。
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(courses)").fetchall()}
    added = False
    if "end_period" not in columns:
        # SQLite 的 ADD COLUMN 不能直接加一个「没有默认值的 NOT NULL」，
        # 所以先给 0，再用下面的 UPDATE 把老数据补成 period。
        conn.execute(
            "ALTER TABLE courses ADD COLUMN end_period INTEGER NOT NULL DEFAULT 0"
        )
        added = True

    # 旧数据：单节课的结束节次 = 开始节次
    conn.execute(
        "UPDATE courses SET end_period = period "
        "WHERE end_period IS NULL OR end_period < 1 OR end_period < period"
    )
    # 新 UI 的节次范围是 1 ~ 11；旧版本允许 12，这里把越界数据截到 11
    conn.execute(
        "UPDATE courses SET period = ? WHERE period > ?", (NUM_PERIODS, NUM_PERIODS)
    )
    conn.execute(
        "UPDATE courses SET end_period = ? WHERE end_period > ?",
        (NUM_PERIODS, NUM_PERIODS),
    )
    conn.execute(
        "UPDATE courses SET end_period = period WHERE end_period < period"
    )
    conn.commit()

    if added:
        print(
            "检测到 courses 表没有 end_period 字段，已自动添加，"
            "并把已有课程按单节课处理（end_period = period）"
        )


def init_db():
    """建表 + 建两个固定用户 + 给新用户放几门示例课程。

    表已存在就跳过，所以重启不会重复插入，也不会清掉已有课表。
    """
    conn = get_db()

    # 1) 旧版（单人版）的数据库直接清空重建，避免后面所有查询都缺 user_id
    reset_outdated_schema(conn)

    # 2) users 表：一个用户一条记录。share_code 是他只读课表的访问码。
    #    UNIQUE 保证用户名不重复、分享码也不会撞。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,   -- 自增主键
            username   TEXT    NOT NULL UNIQUE,             -- 显示名，例如「我」「同学A」
            share_code TEXT    NOT NULL UNIQUE,             -- 只读分享码，例如 x8f2a
            avatar     TEXT    NOT NULL DEFAULT ''          -- 头像路径：avatars/1_xxx.jpg，空 = 还没上传
        )
        """
    )

    # 2.1) 老数据库的 users 表没有 avatar 字段：补上，已有数据保持不变
    migrate_users_avatar(conn)

    # 3) courses 表：user_id 决定这门课属于谁，所有增删改查都按它隔离
    #    period = 开始节次，end_period = 结束节次；单节课两者相等。
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS courses (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,   -- 自增主键
            user_id    INTEGER NOT NULL,                    -- 属于哪个用户（users.id）
            name       TEXT    NOT NULL,                    -- 课程名
            weekday    INTEGER NOT NULL,                    -- 星期几：1 ~ 7
            period     INTEGER NOT NULL,                    -- 开始节次：1 ~ 11
            end_period INTEGER NOT NULL DEFAULT 0,          -- 结束节次：1 ~ 11（老数据由迁移补成 period）
            location   TEXT    NOT NULL DEFAULT '',         -- 上课地点，可以为空
            weeks      TEXT    NOT NULL DEFAULT '1-16',     -- 上课周数：1-16 / 1-16单 / 2-16双 / 1-8,10-16
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )

    # 3.1) 老数据库没有 end_period：平滑补一列，并把老课程当成单节课（end_period = period）
    migrate_courses_end_period(conn)

    # 按用户查课表是最常用的操作，加个索引
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_courses_user ON courses (user_id)"
    )

    # 4) 两个固定用户：只在「users 表还是空的」时候初始化。
    #    share_code 只生成一次，之后一直复用，所以对方的串门链接不会变。
    #    为什么不再按用户名逐个补建：现在页面上可以改昵称，如果这里还按
    #    FIXED_USERS 里的名字补建，用户把「我」改成别的名字后，下次重启就会
    #    凭空多出一个「我」，而且他名下还会带一份示例课程。
    new_usernames = []
    has_user = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None
    if not has_user:
        for username in FIXED_USERS:
            conn.execute(
                "INSERT INTO users (username, share_code) VALUES (?, ?)",
                (username, unique_share_code(conn)),
            )
            new_usernames.append(username)

    # 5) 只给「这次刚建出来」的用户塞示例课程：
    #    以后你把课删空了，重启也不会又冒出来。
    for username in new_usernames:
        demo_courses = DEMO_COURSES.get(username)
        if not demo_courses:
            continue
        user = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        conn.executemany(
            "INSERT INTO courses "
            "(user_id, name, weekday, period, end_period, location, weeks) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(user["id"], *course) for course in demo_courses],
        )

    # 增删改之后一定要 commit，否则数据只存在于内存里，不会写进 .db 文件
    conn.commit()
    conn.close()


def insert_course(user_id, name, weekday, period, end_period, location, weeks=DEFAULT_WEEKS):
    """把一门课写进「某个用户」的课表。

    返回值：1 = 真的新增了一条；0 = 课表里已经有完全一样的一门课。
    手动表单和 AI 解析共用这一段，避免写两遍。
    """
    return insert_courses(user_id, [(name, weekday, period, end_period, location, weeks)])


def insert_courses(user_id, courses):
    """批量写入某个用户的课程，一次提交；返回真正新增的条数。

    courses 是 [(name, weekday, period, end_period, location, weeks), ...]；
    period 是开始节次，end_period 是结束节次（单节课两者相等）。
    user_id 由调用方（当前操作人）传进来，保证课只会落在自己的课表里。
    用 executemany 比逐条 insert 快。

    完全相同的课程（同名 + 同一天 + 同节次 + 同地点 + 同周次）直接跳过：
    这种重复在课表上只会叠出几条一模一样的窄条，没有任何信息量，
    还会把「同一天时间冲突」的并排布局挤成看不清的细丝。
    """
    if not courses:
        return 0

    conn = get_db()
    # 当前用户的课先读出来做比对：课表规模很小，一次查询 + 集合判断就够
    existing = {
        tuple(row)
        for row in conn.execute(
            "SELECT name, weekday, period, end_period, location, weeks "
            "FROM courses WHERE user_id = ?",
            (user_id,),
        )
    }

    rows = []
    for name, weekday, period, end_period, location, weeks in courses:
        key = (name, weekday, period, end_period, location, weeks)
        # 库里已经有的、以及这一批自己重复的，都只保留一条
        if key in existing:
            continue
        existing.add(key)
        rows.append((user_id, name, weekday, period, end_period, location, weeks))

    if rows:
        conn.executemany(
            "INSERT INTO courses "
            "(user_id, name, weekday, period, end_period, location, weeks) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    conn.close()
    return len(rows)


def delete_course(course_id, user_id):
    """删除「当前用户的」某一门课，返回真正删掉的条数（0 表示没删到）。

    条件里带上 user_id：别人的课程 id 就算被猜到也删不掉，做到数据隔离。
    """
    conn = get_db()
    cursor = conn.execute(
        "DELETE FROM courses WHERE id = ? AND user_id = ?", (course_id, user_id)
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def update_course(
    course_id, user_id, name, weekday, period, end_period, location, weeks
):
    """修改「当前用户的」某一门课，返回真正更新的条数（0 表示没改到）。

    和 delete_course 一样，WHERE 里带 user_id：别人的课程 id 就算被人
    手动改成 /edit/别人的课程id，数据库里也一条都匹配不上，改不到别人的课表。
    """
    conn = get_db()
    cursor = conn.execute(
        "UPDATE courses SET name = ?, weekday = ?, period = ?, end_period = ?, "
        "location = ?, weeks = ? WHERE id = ? AND user_id = ?",
        (name, weekday, period, end_period, location, weeks, course_id, user_id),
    )
    updated = cursor.rowcount
    conn.commit()
    conn.close()
    return updated


def delete_all_courses(user_id):
    """清空「某个用户」的全部课程，返回真正删掉的条数。

    WHERE user_id = ? 决定了本次只会删当前操作人自己的课，
    另一个用户的课表一门都不会被动到。
    """
    conn = get_db()
    cursor = conn.execute("DELETE FROM courses WHERE user_id = ?", (user_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def update_username(user_id, username):
    """改「当前用户」的显示名，返回 (是否成功, 错误提示)。

    - WHERE id = ?：id 只来自 session（get_current_user()），改不到别人；
    - users.username 上有 UNIQUE 约束，重名会抛 sqlite3.IntegrityError，
      这里捕获后返回一句人话提示，而不是把 500 页面甩给用户。
    """
    conn = get_db()
    try:
        cursor = conn.execute(
            "UPDATE users SET username = ? WHERE id = ?", (username, user_id)
        )
        updated = cursor.rowcount
        conn.commit()
    except sqlite3.IntegrityError:
        # 重名：回滚这次修改，保持原来的昵称
        conn.rollback()
        return False, "这个昵称已经被占用了，换一个试试"
    finally:
        conn.close()

    if not updated:
        return False, "当前用户不存在，请重新用专属免密链接打开首页"
    return True, ""


def update_user_avatar(user_id, avatar_path):
    """改「当前用户」的头像路径，返回是否真的更新到了（0 = 这个人已经不在了）。

    WHERE id = ?：id 只来自 session（get_current_user()），改不到别人的头像。
    存的是相对 static 的路径（avatars/1_xxx.jpg），不是绝对路径 ——
    这样换机器 / 换域名都不用改数据库。
    """
    conn = get_db()
    cursor = conn.execute(
        "UPDATE users SET avatar = ? WHERE id = ?", (avatar_path, user_id)
    )
    updated = cursor.rowcount
    conn.commit()
    conn.close()
    return bool(updated)


def remove_avatar_file(avatar_path):
    """删掉 static/avatars/ 下的某个头像文件；路径不合法 / 文件不存在都安静跳过。

    只接受「avatars/<纯文件名>」这种写法：带子目录、带 .. 的一律不处理，
    防止有人把别的文件（比如 .env）当成头像路径让它被删掉。
    """
    value = (avatar_path or "").strip()
    if not value.startswith(AVATAR_URL_PREFIX + "/"):
        return

    name = value[len(AVATAR_URL_PREFIX) + 1:]
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return

    try:
        (AVATAR_DIR / name).unlink()
    except OSError:
        pass  # 文件早就没了 / 没权限：不影响这次上传


def sniff_image_extension(raw):
    """按文件头判断图片类型，返回扩展名；不是认识的图片就返回 None。

    不看上传时带的 Content-Type / 文件名：那两个浏览器（或人）都能随便写，
    真正决定存成什么文件的应该是图片内容本身。
    """
    if raw.startswith(b"\xff\xd8\xff"):
        return "jpg"       # JPEG
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"       # PNG
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "gif"       # GIF
    if len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"      # WebP
    return None


def user_avatar_url(user):
    """把 users.avatar 里存的相对路径换成能直接放进 <img src> 的地址。

    没上传过（空字符串）或数据不对劲 → 返回 None，模板就显示「昵称首字母」的占位头像。
    """
    value = (user["avatar"] or "").strip() if user is not None else ""
    if not value.startswith(AVATAR_URL_PREFIX + "/") or ".." in value:
        return None
    return url_for("static", filename=value)


def strip_code_fence(text):
    """剔除模型偶尔加的 Markdown 代码块标记 ```json ... ```。"""
    text = text.strip()
    if text.startswith("```"):
        # "```json\n{...}\n```" 用 ``` 切开，取中间那段
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    return text.strip()


def extract_json(text):
    """从模型返回的文字里取出 JSON（对象或数组都行）。

    模型偶尔会在 JSON 前后加一两句解释，或者外面裹一层 ```json，
    所以依次尝试：整段解析 → 截取第一个 [ 到最后一个 ] → 截取第一个 { 到最后一个 }。
    """
    text = strip_code_fence(text)

    def _loads(chunk):
        try:
            return json.loads(chunk)
        except ValueError:
            return None

    data = _loads(text)
    if data is not None:
        return data

    # 优先按数组截取（批量解析的返回格式是数组）
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        data = _loads(text[start : end + 1])
        if data is not None:
            return data

    # 再按对象截取（兼容模型只返回单个对象的情况）
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        data = _loads(text[start : end + 1])
        if data is not None:
            return data

    raise AIError("模型返回的内容不是有效的 JSON，请再试一次")


def parse_course_items(raw_text):
    """把模型返回的内容整理成课程对象列表。

    兼容三种写法：
        [ {...}, {...} ]              —— 需求要求的标准格式
        {...}                         —— 模型偶尔只返回单个对象
        {"courses": [...]}            —— 模型偶尔包一层
    """
    data = extract_json(raw_text)

    if isinstance(data, dict):
        # 可能是 {"courses": [...]} / {"data": [...]} 这类包装
        for key in ("courses", "data", "list", "items", "result"):
            value = data.get(key)
            if isinstance(value, list):
                data = value
                break
        else:
            # 没有包装，就当成单门课
            data = [data]

    if not isinstance(data, list):
        raise AIError("模型返回的格式不对（需要课程数组），请再试一次")

    return data


def image_to_data_url(image_file):
    """把上传的图片转成 Base64 Data URL。

    智谱接口要求 image_url.url 传 "data:image/jpeg;base64,xxxx" 这种格式。
    这里按上传文件的真实类型生成前缀（png 就用 image/png），拿不到类型时按需求用 image/jpeg。
    """
    mime = (image_file.mimetype or "").lower()
    if not mime.startswith("image/"):
        mime = "image/jpeg"

    raw = image_file.read()
    if not raw:
        raise AIError("上传的图片是空的，请重新选择一张截图")
    if len(raw) > MAX_IMAGE_BYTES:
        raise AIError("图片太大了（超过 5MB），请压缩或截小一点再上传")

    b64 = base64.b64encode(raw).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def parse_course_list(raw_text):
    """把视觉模型返回的文字解析成课程列表。

    模型可能返回 [...]，也可能包一层 ```json，还可能是 {"courses": [...]}，这里都兼容。
    """
    text = strip_code_fence(raw_text)

    try:
        data = json.loads(text)
    except ValueError:
        # 兜底：模型在前面说了几句废话时，从第一个 [ 截到最后一个 ]
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            raise AIError("模型返回的内容不是 JSON 列表，请换一张更清晰的截图重试") from None
        try:
            data = json.loads(text[start : end + 1])
        except ValueError as exc:
            raise AIError("模型返回的 JSON 语法有误，请重试或换一张更清晰的截图") from exc

    if isinstance(data, dict):
        # 少数情况下模型会包成 {"courses": [...]}
        data = data.get("courses") or data.get("data") or data.get("list")
    if not isinstance(data, list):
        raise AIError("模型返回的不是课程列表，请换一张更清晰的截图重试")
    return data


def call_glm_vision(image_data_url):
    """把课表截图（Base64 Data URL）发给智谱 GLM 视觉模型，返回模型输出的原始文字。"""
    api_key = os.getenv("GLM_API_KEY", "").strip()
    if not api_key:
        raise AIError(
            "没有找到 GLM_API_KEY。请在平台的环境变量里添加 GLM_API_KEY"
            "（本地开发写在项目根目录的 .env 里），然后重启服务"
        )

    payload = {
        "model": GLM_MODEL,
        "messages": [
            # 系统提示词：规定只返回 JSON 列表
            {"role": "system", "content": IMAGE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    # 图片理解：文本 + 图片两个内容块
                    {"type": "text", "text": "请识别这张课表截图里的课程。"},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            },
        ],
        # 智谱的 temperature 必须在 (0, 1) 区间，不能写 0
        "temperature": 0.1,
    }

    try:
        resp = requests.post(
            GLM_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            # 视觉模型要十几秒，60 秒超时防止请求被提前掐断
            timeout=IMAGE_TIMEOUT,
        )
    except requests.Timeout as exc:
        raise AIError("识别图片超时了（超过 60 秒），请换一张更小的截图再试") from exc
    except requests.RequestException as exc:
        raise AIError(f"网络请求失败：{exc}") from exc

    if resp.status_code == 401:
        raise AIError("GLM API Key 无效（401），请检查平台环境变量（本地开发是 .env）里的 GLM_API_KEY")
    if resp.status_code == 429:
        raise AIError("调用太频繁或额度不足（429），请稍后再试")
    if resp.status_code != 200:
        raise AIError(f"智谱接口返回 {resp.status_code}：{resp.text[:200]}")

    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError("解析智谱返回的数据结构失败，请重试") from exc


def parse_course_with_ai(text):
    """调用 DeepSeek，把一段自然语言解析成课程对象列表。

    返回值一定是 list，里面每个元素是 {name, weekday, period, location, weeks}；
    即使模型只返回单个对象，也会被 parse_course_items() 包成只含一个元素的列表。

    DeepSeek 用的是 OpenAI 兼容接口，所以就是普通的 HTTP POST：
        请求头带 Authorization: Bearer <API Key>
        请求体里放 messages
    """
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise AIError(
            "没有找到 DEEPSEEK_API_KEY。请在平台的环境变量里添加 DEEPSEEK_API_KEY"
            "（本地开发写在项目根目录的 .env 里），然后重启服务"
        )

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": AI_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0,  # 0 = 输出尽量稳定，同一句话每次结果基本一致
        # 注意：这里不能用 response_format={"type": "json_object"}。
        # 该模式只保证输出一个 JSON 对象，而批量解析需要顶层是 JSON 数组，
        # 所以改成「提示词要求数组 + parse_course_items() 兜底解析」。
    }

    try:
        resp = requests.post(
            DEEPSEEK_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.Timeout as exc:
        raise AIError("调用 DeepSeek 超时了，请稍后再试一次") from exc
    except requests.RequestException as exc:
        raise AIError(f"网络请求失败：{exc}") from exc

    if resp.status_code == 401:
        raise AIError("API Key 无效（401），请检查平台环境变量（本地开发是 .env）里的 DEEPSEEK_API_KEY")
    if resp.status_code == 402:
        raise AIError("DeepSeek 账户余额不足（402），请先充值")
    if resp.status_code != 200:
        raise AIError(f"DeepSeek 接口返回 {resp.status_code}：{resp.text[:200]}")

    try:
        content = resp.json()["choices"][0]["message"]["content"]
        # 兼容数组 / 单个对象 / {"courses": [...]}，统一整理成列表
        return parse_course_items(content)
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise AIError("解析模型返回的内容失败，请再试一次") from exc


def normalize_weekday(value):
    """把 3 / "3" / "星期三" / "周三" / "Wednesday" 统一成 1~7 的整数；认不出返回 0。

    视觉模型（尤其 glm-4v-flash）不一定老老实实按提示词给数字，
    所以这里把常见的星期写法都兜住。
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, (list, tuple)):
        return normalize_weekday(value[0]) if value else 0

    text = str(value).strip().lower()
    if text.lstrip("+-").isdigit():
        return int(text)

    # 中文「星期三/周三/礼拜三」和英文缩写都能命中
    for word, number in WEEKDAY_WORDS.items():
        if word in text:
            return number
    return 0


def normalize_period(value):
    """把 6 / "6" / "第5-6节" / [5, 6] 统一成整数；认不出返回 0。

    连堂课（第 5-6 节）这里只取区间的第一节（5），
    结束节次交给 normalize_period_end() 单独解析。
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, (list, tuple)):
        for item in value:
            period = normalize_period(item)
            if period:
                return period
        return 0

    text = str(value).strip()
    if text.lstrip("+-").isdigit():
        return int(text)

    # "第5-6节" / "5、6" / "5-6" 这类，取第一个数字
    numbers = re.findall(r"\d+", text)
    if numbers:
        return int(numbers[0])

    # 兜底：「第二节」这种中文数字
    if "十二" in text:
        return 12
    if "十一" in text:
        return 11
    if "十" in text:
        return 10
    for char, number in CN_DIGITS.items():
        if char in text:
            return number
    return 0


def normalize_period_end(value, period):
    """解析「结束节次」：支持 4 / "4" / "第3-4节" / [3, 4] / "第4节"。

    和 normalize_period() 的区别：这里取区间里的「最大数字」。
    例如 AI 把 period 写成 "1-2"、把 end_period 写成 "4"，这里会拿到 4。
    认不出来（None / 0 / 空字符串）时退回开始节次，表示这是一节单节课。
    """
    if value is None or isinstance(value, bool):
        return period

    numbers = []
    if isinstance(value, (int, float)):
        numbers = [int(value)]
    elif isinstance(value, (list, tuple)):
        for item in value:
            number = normalize_period(item)
            if number:
                numbers.append(number)
    else:
        text = str(value).strip()
        numbers = [int(raw) for raw in re.findall(r"\d+", text)]

    numbers = [number for number in numbers if number >= 1]
    if not numbers:
        return period
    return max(period, min(max(numbers), NUM_PERIODS))


def _parse_week_segment(segment):
    """解析单个周数片段，返回规范化的片段；实在认不出就返回空字符串。

    例如 "1-8周" -> "1-8"、"1-16单周" -> "1-16单"、"第3周" -> "3"。
    """
    text = segment.translate(FULLWIDTH_DIGITS).strip()

    # 各种连接符 / 中文「到、至」统一成半角减号，方便后面取两个端点
    for mark in ("~", "～", "—", "–", "－", "到", "至"):
        text = text.replace(mark, "-")

    # 单周 / 双周：两种都提到（「单双周都有」）就当成全周
    if "单" in text and "双" in text:
        parity = ""
    elif "单" in text:
        parity = "单"
    elif "双" in text:
        parity = "双"
    else:
        parity = ""

    numbers = []
    for raw in re.findall(r"\d+", text):
        number = int(raw)
        if number < 1:
            continue
        numbers.append(min(number, MAX_WEEK))  # 超过 20 周按 20 截断

    if not numbers:
        # 只说了「单周」「双周」而没给范围：按默认的 1~16 周算
        return f"{DEFAULT_WEEKS}{parity}" if parity else ""

    if len(numbers) == 1:
        start = numbers[0]
        # 「从第 3 周开始的单周」没给结束周，就按默认的 16 周算
        end = max(start, DEFAULT_WEEK_END) if parity else start
    else:
        start, end = sorted(numbers[:2])

    # 只有一周时写成 "3"，不要把 "3-3" 存进数据库
    if start == end:
        return str(start)
    return f"{start}-{end}{parity}"


def normalize_weeks(value):
    """把各种周数写法统一成前端能解析的字符串（见文件顶部的格式说明）。

    能认出来的写法：
        "1-8周" / "第 1 到 16 周" / "1-16周单周" / "单周" / "双周"
        "1-8,10-16" / "1、3、5、7" / "3" / [1, 3, 5] / 8
    什么也认不出来（包括原文没提周数）时返回 DEFAULT_WEEKS（"1-16"），
    这样数据库里不会出现空值，前端也不用处理特殊情况。
    """
    if value is None or isinstance(value, bool):
        return DEFAULT_WEEKS
    if isinstance(value, (int, float)):
        week = int(value)
        return str(week) if 1 <= week <= MAX_WEEK else DEFAULT_WEEKS
    if isinstance(value, (list, tuple)):
        parts = [normalize_weeks(item) for item in value]
        parts = [part for part in parts if part]
        return ",".join(parts) if parts else DEFAULT_WEEKS

    text = str(value).translate(FULLWIDTH_DIGITS).strip()
    if not text:
        return DEFAULT_WEEKS

    parsed = []
    # 逗号 / 顿号 / 分号 / 斜杠都当成分段符（"1-8,10-16"、"1、3、5"）
    for segment in re.split(r"[,，、;；/]+", text):
        segment = segment.strip()
        if not segment:
            continue
        item = _parse_week_segment(segment)
        if item:
            parsed.append(item)

    if not parsed:
        return DEFAULT_WEEKS

    # 去重但保持顺序（"1-16,1-16" 只留一个）
    return ",".join(dict.fromkeys(parsed))


def format_weeks(weeks_text):
    """把规范化后的周数转成给人看的写法，用在页面和提示语里。

    "1-16" -> "第 1-16 周"；"1-16单" -> "第 1-16 周（单周）"；"1-8,10-16" -> "第 1-8 周、第 10-16 周"
    """
    parsed = normalize_weeks(weeks_text)
    parts = []
    for item in parsed.split(","):
        item = item.strip()
        if not item:
            continue
        note = ""
        if item.endswith("单"):
            item, note = item[:-1], "（单周）"
        elif item.endswith("双"):
            item, note = item[:-1], "（双周）"
        parts.append(f"第 {item} 周{note}")
    return "、".join(parts) if parts else f"第 {DEFAULT_WEEKS} 周"


# 模板里可以直接写 {{ course['weeks']|format_weeks }}，显示成「第 1-16 周」
app.jinja_env.filters["format_weeks"] = format_weeks


def format_period_range(period, end_period):
    """把开始/结束节次显示成「第 3 节」或「第 3-4 节」。"""
    if end_period and end_period > period:
        return f"第 {period}-{end_period} 节"
    return f"第 {period} 节"


def period_range_from_form(form):
    """从手动表单里读出 (开始节次, 结束节次, 错误提示)。

    规则：
        - 开始节次必填，范围 1 ~ 11；
        - 结束节次选填（老页面 / 脚本没传时按单节课处理）；
        - 结束节次小于开始节次时自动抬到开始节次，不因为手滑报错；
        - 结束节次超过 11 时截到 11。
    """
    raw_period = (form.get("period") or "").strip()
    try:
        period = int(raw_period)
    except (TypeError, ValueError):
        return None, None, "开始节次必须是数字"

    if not 1 <= period <= NUM_PERIODS:
        return None, None, f"开始节次只能是 1 ~ {NUM_PERIODS}"

    raw_end = (form.get("end_period") or "").strip()
    try:
        end_period = int(raw_end) if raw_end else period
    except (TypeError, ValueError):
        end_period = period

    end_period = max(period, min(end_period, NUM_PERIODS))
    return period, end_period, None


def clean_course(data):
    """把解析结果校验成数据库能用的六个值，不合格就抛 AIError 让页面提示。"""
    name = str(data.get("name") or "").strip()
    location = str(data.get("location") or "").strip()
    weekday = normalize_weekday(data.get("weekday"))
    period = normalize_period(data.get("period"))
    end_period = normalize_period_end(data.get("end_period"), period)
    weeks = normalize_weeks(data.get("weeks"))

    if not name:
        raise AIError("没能解析出课程名，换个说法试试，例如：周三下午第二节课在图书馆上英语")
    if not 1 <= weekday <= 7:
        raise AIError("没能解析出是星期几（1~7），换个说法试试")
    if not 1 <= period <= NUM_PERIODS:
        raise AIError(f"没能解析出是第几节课（1~{NUM_PERIODS}），换个说法试试")
    if not period <= end_period <= NUM_PERIODS:
        raise AIError(f"结束节次必须在 {period} ~ {NUM_PERIODS} 之间，换个说法试试")

    return {
        "name": name,
        "weekday": weekday,
        "period": period,
        "end_period": end_period,
        "location": location,
        "weeks": weeks,
    }


def get_current_week(today=None):
    """按真实日期算出「今天是第几周」，给首页 / 只读页的周次滑块当默认值。

    算法：第几周 = (今天 - 开学日期).days // 7 + 1，结果限制在 1 ~ MAX_WEEK：
        - 还没开学（今天早于开学日期）→ 第 1 周；
        - 学期已结束（算出来超过 MAX_WEEK）→ 第 MAX_WEEK 周，不会超出滑块范围；
        - SEMESTER_START_DATE 格式写错 → 退回第 1 周，只记一条警告，页面照常打开。

    today 参数只是为了方便测试 / 以后想按别的时间算，正常调用不用传。
    """
    if today is None:
        today = date.today()

    try:
        semester_start = datetime.strptime(SEMESTER_START_DATE, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        app.logger.warning(
            "SEMESTER_START_DATE 格式不对（应为 YYYY-MM-DD）：%r，本次按第 1 周显示",
            SEMESTER_START_DATE,
        )
        return 1

    week = (today - semester_start).days // 7 + 1
    return max(1, min(MAX_WEEK, week))


def _schedule_grid_row(period):
    """节次 → CSS Grid 行号：第 1 行是表头，第 4 节后多一行「中午」。"""
    return period + 1 + (1 if period > LUNCH_AFTER_PERIOD else 0)


def _assign_course_lanes(courses):
    """给同一天互相重叠的课程分配左右「车道」，避免卡片直接叠在一起。

    只处理同一天：不同天在不同列，永远不会重叠。
    两门课节次区间有交集才算冲突，例如 1-2 节和 2-3 节需要并排；
    完全不冲突（1-2 节和 3-4 节）仍然各占整列宽度。
    """
    by_day = {}
    for course in courses:
        by_day.setdefault(course["weekday"], []).append(course)

    for day_courses in by_day.values():
        day_courses.sort(
            key=lambda course: (
                course["period"],
                course["end_period"],
                course["id"],
            )
        )

        # 先把互相有交集（直接或间接）的课程分成一簇，簇内再分车道
        clusters = []
        cluster = []
        cluster_end = 0
        for course in day_courses:
            if cluster and course["period"] > cluster_end:
                clusters.append(cluster)
                cluster = []
            if not cluster:
                cluster_end = course["end_period"]
            else:
                cluster_end = max(cluster_end, course["end_period"])
            cluster.append(course)
        if cluster:
            clusters.append(cluster)

        for cluster in clusters:
            lane_ends = []
            for course in cluster:
                for lane, lane_end in enumerate(lane_ends):
                    if lane_end < course["period"]:
                        lane_ends[lane] = course["end_period"]
                        course["lane"] = lane
                        break
                else:
                    course["lane"] = len(lane_ends)
                    lane_ends.append(course["end_period"])
            for course in cluster:
                course["lanes"] = len(lane_ends)


def load_schedule(user_id):
    """读出「某个用户」的所有课程，并算好每张卡片在 CSS Grid 里的位置。

    WHERE user_id = ? 就是数据隔离的关键：只查当前操作人自己的课。

    返回的每门课都带这些渲染字段：
        period / end_period —— 开始 / 结束节次
        grid_row / grid_span —— 卡片从第几行开始、跨几行（自动跨过「中午」）
        grid_column          —— 星期几 → 第几列（左边时间轴占第 1 列）
        lane / lanes         —— 同一天课程冲突时并排显示的序号 / 总车道数

    首页（可编辑）和只读页（串门）都用这一份数据，避免两边逻辑不一致。
    """
    conn = get_db()
    # 把 id 也查出来：首页的删除按钮要靠它对上数据库里的那一条
    rows = conn.execute(
        "SELECT id, name, weekday, period, end_period, location, weeks "
        "FROM courses WHERE user_id = ? ORDER BY weekday, period, end_period, id",
        (user_id,),
    ).fetchall()
    conn.close()

    courses = []
    for row in rows:
        period = max(1, min(int(row["period"]), NUM_PERIODS))
        end_period = int(row["end_period"] or period)
        end_period = max(period, min(end_period, NUM_PERIODS))
        weekday = max(1, min(int(row["weekday"]), 7))

        courses.append(
            {
                "id": row["id"],
                "name": row["name"],
                "weekday": weekday,
                "period": period,
                "end_period": end_period,
                "location": row["location"],
                "weeks": row["weeks"],
                "grid_row": _schedule_grid_row(period),
                "grid_span": _schedule_grid_row(end_period)
                - _schedule_grid_row(period)
                + 1,
                "grid_column": weekday + 1,
            }
        )

    _assign_course_lanes(courses)
    return courses


def render_index(bind_error=None, **extra):
    """首页的通用渲染：确认当前操作人 → 只读他的课 → 拼成表格 → 交给模板。

    / 和 /add-image 都要渲染首页，所以把这段共用逻辑抽出来，
    额外要传给模板的变量用关键字参数传进来（例如 pending_courses=...）。
    bind_error：专属免密链接里的分享码对不上时的提示文字。
    注意：get_current_user() 现在在 session 里没有身份时直接 403，
          不会再把访问者默认绑成「我」；/ 路由会先拦住没身份的人。
    """
    current_user = get_current_user()
    courses = load_schedule(current_user["id"])
    users = get_users()

    # 串门：找一个「不是自己」的用户，按钮直接链到他的只读课表
    others = [user for user in users if user["id"] != current_user["id"]]
    partner = others[0] if others else None

    context = {
        "weekdays": WEEKDAYS,
        "courses": courses,
        "period_rows": PERIOD_ROWS,
        "num_periods": NUM_PERIODS,
        "lunch_row": LUNCH_ROW,
        "lunch_after_period": LUNCH_AFTER_PERIOD,
        # 周数切换：滑块最大值；default_week 是后端按真实日期算出的「今天是第几周」，
        # 前端打开页面时先把滑块拖到这一周，只显示本周的课。
        "max_week": MAX_WEEK,
        "default_week": get_current_week(),
        "default_weeks": DEFAULT_WEEKS,
        # 身份：当前操作人 + 串门对象（不再有下拉框，身份由打开的链接决定）
        "users": users,
        "current_user": current_user,
        # 头像：users.avatar 里存的是相对 static 的路径（avatars/xxx.jpg），
        # 这里换成能直接写进 <img src> 的地址；没上传过就是 None（模板里显示昵称首字母）
        "avatar_url": user_avatar_url(current_user),
        "partner": partner,
        "partner_view_url": (
            url_for("view_schedule", share_code=partner["share_code"])
            if partner
            else None
        ),
        # 分享信息：每个用户有自己的 share_code，模板顶部用它显示「复制链接」。
        # url_for 加 _external=True 会自动带上当前访问的域名和端口。
        "share_code": current_user["share_code"],
        # 专属免密链接 /?u=<code>：打开就直接是「我的身份」，能改自己的课表。
        # 注意：这个链接等于密码，页面上要提醒「别随便发给只想看看的同学」。
        "magic_url": url_for(
            "index", u=current_user["share_code"], _external=True
        ),
        # 只读链接 /view/<code>：给同学看最安全，页面里没有任何表单
        "readonly_url": url_for(
            "view_schedule", share_code=current_user["share_code"], _external=True
        ),
        # 从 ?error=xxx / ?message=xxx 里取提示；专属链接失效时优先显示那条
        "error": bind_error or request.args.get("error"),
        "message": request.args.get("message"),
        # 图片识别出来、还没入库的课程（有值时才显示「识别结果确认」区块）
        "pending_courses": [],
        "skipped": 0,
    }
    context.update(extra)
    return render_template("index.html", **context)


def render_share(user):
    """只读页的渲染：看的是 user 的课表，只读，不传任何表单相关的东西。

    安全约定（两层）：
        1) 这里只用 peek_current_user()：它只读 session，既不写 session，
           也不会像 get_current_user() 那样在 session 为空时默认绑成第一个用户「我」；
        2) 「回首页」链接必须带上**访问者自己的 share_code**（下面的 home_url），
           绝不生成裸链接 / —— 裸链接在 session 为空时会被首页默认绑成「我」，
           等于把「我」的编辑权限送给只是来看课表的同学。
    """
    courses = load_schedule(user["id"])
    viewer = peek_current_user()
    is_own = bool(viewer and viewer["id"] == user["id"])

    # 首页地址一律写成 /?u=<访问者自己的 share_code>：
    #   - 自己看自己的只读页 → 绑回自己，回首页编辑自己的课表；
    #   - 别人看我的只读页   → 绑的是访问者自己，回的是「他自己的」课表；
    #   - 没有身份（未绑定） → home_url 为 None，模板里干脆不显示「回首页」。
    # 注意：无论哪种情况都不会出现裸链接 /。
    home_url = url_for("index", u=viewer["share_code"]) if viewer else None

    return render_template(
        "share.html",
        # owner / viewer / is_own 让模板显示「谁的课表」「你是谁」
        owner=user,
        viewer=viewer,
        is_own=is_own,
        # 模板里的「回首页 / 回我的课表」只能用这个地址，不要自己拼 /
        home_url=home_url,
        weekdays=WEEKDAYS,
        courses=courses,
        period_rows=PERIOD_ROWS,
        num_periods=NUM_PERIODS,
        lunch_row=LUNCH_ROW,
        lunch_after_period=LUNCH_AFTER_PERIOD,
        max_week=MAX_WEEK,
        # 只读页同样自动定位到「当前是第几周」
        default_week=get_current_week(),
        default_weeks=DEFAULT_WEEKS,
    )


def render_no_identity(bind_error=None):
    """没有身份时的提示页：请访问者用带 ?u= 的专属链接进来，而不是把他默认绑成「我」。

    返回 403：这是一个「还没被授权」的页面，浏览器 / 爬虫也能看出这里不是正常课表。
    bind_error：?u= 里的分享码对不上时，把原因一起显示出来（刷新就没了）。
    """
    return (
        render_template("no_identity.html", error=bind_error),
        403,
    )


@app.route("/")
def index():
    """首页：只显示「当前操作人」的课程，按「第几节 × 星期几」填进表格。

    身份识别（免密，取代原来的下拉框）：
        带 ?u=<share_code>  → 把 session 里的身份绑成这个分享码对应的用户（点开即看）
        不带 ?u=             → 沿用 session 里已有的身份
        两者都没有           → 不绑成任何人，只显示「请用专属链接访问」的提示页（403）
    这里只把 ?u= 当成「认人」的入口，所有写操作都只看 session，
    所以换个 URL 参数最多是「换一个身份打开页面」，而不是给正在编辑的人加权限。
    """
    bind_error = None
    raw_code = (request.args.get("u") or "").strip().lower()

    if raw_code:
        if bind_user_by_share_code(raw_code) is None:
            # 分享码是 6 位，这里截断一下，避免把超长参数原样回显到页面上
            shown = raw_code[:12]
            bind_error = f"链接里的身份码「{shown}」不存在，请检查链接是否完整"

    # 关键：处理完 ?u= 之后 session 里还是没有身份（没带参数 / 参数无效）
    #       → 绝不退回第一个用户「我」，只显示提示页，让访问者自己用专属链接进来。
    if peek_current_user() is None:
        return render_no_identity(bind_error)

    return render_index(bind_error=bind_error)


@app.route("/view/<share_code>")
def view_schedule(share_code):
    """串门：用对方的 share_code 打开他「只读」的课表（同样支持周数切换）。

    安全要点：
        1) code 和 users 表里的 share_code 对不上就返回 404，随便试地址打不开；
        2) 这个路由是**纯只读**的：不读 ?u=、不写 session、不调用 get_current_user()，
           所以打开只读链接绝不会把访问者绑成 owner（也不会绑成任何身份）；
        3) READONLY_ENDPOINTS 里注册了本路由，before_request 会先记下 session 里的
           身份、after_request 再核对一遍：万一处理过程中 user_id 被改了，返回前
           自动还原（见 snapshot_identity_on_readonly_pages /
           restore_identity_on_readonly_pages）；
        4) 模板 share.html 里没有任何 form / 上传 / 删除入口，「回首页」链接也只会
           带上访问者自己的分享码（/?u=访问者的码），不会生成裸链接 /。
    """
    user = get_user_by_share_code(share_code.strip().lower())
    if user is None:
        abort(404)
    return render_share(user)


@app.route("/share/<share_code>")
def share_schedule(share_code):
    """老链接兼容：/share/<code> 和 /view/<code> 是同一个页面（直接复用上面的逻辑）。"""
    return view_schedule(share_code)


@app.route("/add", methods=["POST"])
def add_course():
    """手动表单：接收精确填写的课程，存进数据库，再跳回首页。"""
    name = request.form.get("name", "").strip()
    location = request.form.get("location", "").strip()
    # 周数允许随便写，统一交给 normalize_weeks() 整理；留空就是默认的 1-16 周
    weeks = normalize_weeks(request.form.get("weeks", ""))

    # 1) 先做简单校验，避免把脏数据写进数据库
    if not name:
        return redirect(url_for("index", error="课程名不能为空"))

    try:
        weekday = int(request.form.get("weekday", ""))
    except ValueError:
        return redirect(url_for("index", error="星期和节次必须是数字"))

    period, end_period, period_error = period_range_from_form(request.form)
    if period_error:
        return redirect(url_for("index", error=period_error))

    if not 1 <= weekday <= 7:
        return redirect(url_for("index", error="星期几只能是 1 ~ 7"))

    # 2) 写库（? 占位符由 sqlite3 处理，可以防住 SQL 注入）
    #    带上当前操作人的 id：这门课只会出现在他自己的课表里
    current_user = get_current_user()
    inserted = insert_course(
        current_user["id"], name, weekday, period, end_period, location, weeks
    )

    # 2.1) 完全一样的课已经在课表里：不重复添加，直接回一句人话
    if not inserted:
        return redirect(
            url_for(
                "index",
                error=f"课表里已经有「{name}」了（{WEEKDAYS[weekday - 1]} "
                f"{format_period_range(period, end_period)}），没有重复添加",
            )
        )

    # 3) 重定向回首页（POST-Redirect-GET 模式）
    #    这样用户刷新页面时不会再重复提交一次表单
    message = (
        f"已添加：{name}（{WEEKDAYS[weekday - 1]} "
        f"{format_period_range(period, end_period)}，{format_weeks(weeks)}）"
    )
    return redirect(url_for("index", message=message))


@app.route("/add-text", methods=["POST"])
def add_by_text():
    """自然语言批量录入：把用户粘贴的整段文字交给 DeepSeek 解析，再逐门入库。"""
    raw_text = request.form.get("text", "").strip()

    if not raw_text:
        return redirect(
            url_for(
                "index",
                error="请先输入课程描述，例如：周一上午1-2节高数 / 周三下午5-6节英语",
            )
        )
    if len(raw_text) > 2000:
        return redirect(url_for("index", error="描述太长了，请控制在 2000 字以内"))

    # 1) 调用 AI：返回的是课程列表（可能多门课）
    try:
        items = parse_course_with_ai(raw_text)
    except AIError as exc:
        return redirect(url_for("index", error=str(exc)))

    # 2) 逐门校验；某一条格式不对就跳过，不影响其它课程
    valid_courses = []
    skipped = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        try:
            course = clean_course(item)
        except AIError:
            skipped += 1
            continue
        valid_courses.append(
            (
                course["name"],
                course["weekday"],
                course["period"],
                course["end_period"],
                course["location"],
                course["weeks"],
            )
        )

    if not valid_courses:
        detail = f"（跳过了 {skipped} 条格式不对的内容）" if skipped else ""
        return redirect(
            url_for(
                "index",
                error=f"没有解析出可用的课程{detail}，请换个说法再试一次",
            )
        )

    # 3) 批量入库，并绑定「当前操作人」，AI 识别出来的课同样隔离
    #    完全相同的课已经在课表里时，insert_courses 会跳过，不重复写
    current_user = get_current_user()
    inserted = insert_courses(current_user["id"], valid_courses)
    duplicated = len(valid_courses) - inserted

    # 4) 绿字提示：批量添加成功（重复的、格式不对的分别说明跳过了几条）
    if not inserted:
        return redirect(
            url_for("index", error="这些课程都已经在课表里了，没有重复添加")
        )

    message = f"成功批量添加了 {inserted} 门课程"
    if duplicated:
        message += f"（另外 {duplicated} 门已经在课表里，已跳过）"
    if skipped:
        message += f"（另有 {skipped} 条格式不对，已跳过）"
    return redirect(url_for("index", message=message))


@app.route("/add-image", methods=["POST"])
def add_image():
    """上传课表截图 → 转 Base64 → 智谱视觉模型识别 → 只展示结果，等用户确认。

    注意：这里**不写数据库**。识别结果会带着 pending_courses 一起渲染回首页，
    用户在「识别结果确认」区块里核对/修改后，再提交到 /confirm-image 才真正入库。
    """
    image = request.files.get("image")
    if image is None or not image.filename:
        return redirect(url_for("index", error="请先选择一张课表截图再提交"))

    try:
        # 1) 图片转 Base64 Data URL（带 data:image/jpeg;base64, 前缀）
        data_url = image_to_data_url(image)
        # 2) 调智谱视觉模型，拿回模型输出的原始文字
        raw_text = call_glm_vision(data_url)
        # 3) 剔除 ``` 标记后解析成课程列表
        items = parse_course_list(raw_text)
    except AIError as exc:
        return redirect(url_for("index", error=str(exc)))

    # 4) 逐条校验；某一条格式不对就跳过，不影响其它课程
    valid_courses = []
    skipped = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        try:
            course = clean_course(item)
        except AIError:
            skipped += 1
            continue
        valid_courses.append(
            (
                course["name"],
                course["weekday"],
                course["period"],
                course["end_period"],
                course["location"],
                course["weeks"],
            )
        )

    if not valid_courses:
        return redirect(
            url_for(
                "index",
                error=f"没有从图片里识别出可用的课程（跳过了 {skipped} 条），请换一张更清晰的截图",
            )
        )

    # 5) 不入库，直接把结果交给前端确认。
    #    用 dict 列表（就是 JSON 的结构）传给模板，方便逐条编辑。
    pending_courses = [
        {
            "name": name,
            "weekday": weekday,
            "period": period,
            "end_period": end_period,
            "location": location,
            "weeks": weeks,
        }
        for name, weekday, period, end_period, location, weeks in valid_courses
    ]
    message = f"识别出 {len(pending_courses)} 门课程，请核对下面的「识别结果确认」后点击「确认保存」"
    return render_index(pending_courses=pending_courses, skipped=skipped, message=message)


@app.route("/confirm-image", methods=["POST"])
def confirm_image():
    """用户在「识别结果确认」区块里核对（可修改）后，把课程真正写进数据库。

    前端是一个表格，每行 6 个同名字段，所以用 getlist() 按顺序取出来再对齐。
    这里按下标逐行取值而不是 zip()：即使浏览器缓存了旧页面、没提交 end_period，
    也能把这一行当成单节课保存，不会因为少一个字段就把整批课程丢掉。
    """
    form = request.form
    names = form.getlist("name")
    weekdays = form.getlist("weekday")
    periods = form.getlist("period")
    end_periods = form.getlist("end_period")
    locations = form.getlist("location")
    weeks_list = form.getlist("weeks")

    valid_courses = []
    skipped = 0

    for index, name in enumerate(names):
        def field(values, fallback=""):
            return values[index] if index < len(values) else fallback

        try:
            course = clean_course(
                {
                    "name": name,
                    "weekday": field(weekdays),
                    "period": field(periods),
                    "end_period": (
                        end_periods[index]
                        if index < len(end_periods) and end_periods[index].strip()
                        else field(periods)
                    ),
                    "location": field(locations),
                    "weeks": field(weeks_list),
                }
            )
        except AIError:
            skipped += 1
            continue
        valid_courses.append(
            (
                course["name"],
                course["weekday"],
                course["period"],
                course["end_period"],
                course["location"],
                course["weeks"],
            )
        )

    if not valid_courses:
        return redirect(url_for("index", error="没有可保存的课程，请检查课程名、星期和节次是否填写正确"))

    # 保存到「当前操作人」名下，AI 识别结果同样隔离；
    # 完全一样的课已经在课表里时会被 insert_courses 跳过
    current_user = get_current_user()
    inserted = insert_courses(current_user["id"], valid_courses)
    duplicated = len(valid_courses) - inserted

    if not inserted:
        return redirect(
            url_for("index", error="这些课程都已经在课表里了，没有重复保存")
        )

    message = f"已保存 {inserted} 门课程"
    if duplicated:
        message += f"（另外 {duplicated} 门已经在课表里，已跳过）"
    if skipped:
        message += f"（另有 {skipped} 行填写不完整，已跳过）"
    return redirect(url_for("index", message=message))


@app.route("/delete/<int:course_id>", methods=["POST"])
def remove_course(course_id):
    """删除一门课，返回 JSON 给前端；前端拿到成功状态后自己刷新页面。

    删除条件里带上当前操作人的 id：即使有人手动构造请求去删别人的课程，
    数据库里也一条都匹配不上，只会返回 404。
    """
    current_user = get_current_user()
    if not delete_course(course_id, current_user["id"]):
        return jsonify(ok=False, error="这门课不存在，或者不属于当前操作人"), 404
    return jsonify(ok=True, id=course_id)


@app.route("/edit/<int:course_id>", methods=["POST"])
def edit_course(course_id):
    """修改一门课（课程名 / 地点 / 周数），保存后跳回首页（POST-Redirect-GET）。

    前端是课表卡片上的 ✏️ 小按钮弹出的模态框，只会提交 name / location / weeks，
    外加两个 hidden 字段 weekday / period（保持原来的星期和节次不变）。

    安全：
        1) 身份只来自 session（get_current_user()，没有身份直接 403）；
        2) 表单里没有任何 user_id / share_code / u 之类的身份参数
           （真有也会被 before_request 的 reject_identity_override_on_writes 拦成 400）；
        3) UPDATE 的 WHERE 里再带一次 user_id，猜别人的课程 id 也改不到。
    """
    current_user = get_current_user()

    name = request.form.get("name", "").strip()
    location = request.form.get("location", "").strip()
    # 周数允许随便写，统一交给 normalize_weeks() 整理；留空就是默认的 1-16 周
    weeks = normalize_weeks(request.form.get("weeks", ""))

    # 1) 基本校验，和 /add 保持一致的口径，避免把脏数据写进数据库
    if not name:
        return redirect(url_for("index", error="课程名不能为空"))
    if len(name) > MAX_COURSE_NAME_LENGTH:
        return redirect(
            url_for("index", error=f"课程名太长了，请控制在 {MAX_COURSE_NAME_LENGTH} 字以内")
        )
    if len(location) > MAX_LOCATION_LENGTH:
        return redirect(
            url_for("index", error=f"地点太长了，请控制在 {MAX_LOCATION_LENGTH} 字以内")
        )

    try:
        weekday = int(request.form.get("weekday", ""))
    except ValueError:
        return redirect(url_for("index", error="星期和节次必须是数字"))

    period, end_period, period_error = period_range_from_form(request.form)
    if period_error:
        return redirect(url_for("index", error=period_error))

    if not 1 <= weekday <= 7:
        return redirect(url_for("index", error="星期几只能是 1 ~ 7"))

    # 2) 写库：id 和 user_id 必须同时对上，才不会改到别人的课
    if not update_course(
        course_id,
        current_user["id"],
        name,
        weekday,
        period,
        end_period,
        location,
        weeks,
    ):
        return redirect(url_for("index", error="这门课不存在，或者不属于当前用户"))

    # 3) 回到首页并提示改了什么
    message = (
        f"已保存修改：{name}（{WEEKDAYS[weekday - 1]} "
        f"{format_period_range(period, end_period)}，{format_weeks(weeks)}）"
    )
    return redirect(url_for("index", message=message))


@app.route("/clear", methods=["POST"])
def clear_schedule():
    """一键清空「当前用户」的全部课程，然后回到首页。

    页面上的红色按钮会先弹窗确认，确认后提交这个表单。
    安全：只删 session 里这个人的课（DELETE ... WHERE user_id = ?），
          请求里没有任何身份参数，所以清空不到别人的课表。
    """
    current_user = get_current_user()
    deleted = delete_all_courses(current_user["id"])

    if not deleted:
        return redirect(url_for("index", message="你的课表本来就是空的，没有需要清空的课程"))
    return redirect(url_for("index", message=f"已清空课表，共删除 {deleted} 门课程"))


@app.route("/update-name", methods=["POST"])
def update_name():
    """修改「当前用户」的昵称（users.username），保存后跳回首页。

    ★ 表单字段特意叫 nickname，而不是 username：
      before_request 的 reject_identity_override_on_writes() 会把 POST 里出现的
      username / user_id / share_code / u 当成「试图指定别人的身份」，直接 400。
      所以这里收 nickname，真正改谁由 session 里的 user_id 决定，改不到别人。
    """
    current_user = get_current_user()
    nickname = request.form.get("nickname", "").strip()

    if not nickname:
        return redirect(url_for("index", error="昵称不能为空"))
    if len(nickname) > MAX_USERNAME_LENGTH:
        return redirect(
            url_for("index", error=f"昵称太长了，请控制在 {MAX_USERNAME_LENGTH} 字以内")
        )
    # 换行 / 制表符 / 其它控制字符会把页面排版搞乱，直接拒绝
    if re.search(r"[\x00-\x1f\x7f]", nickname):
        return redirect(url_for("index", error="昵称里不能包含换行或其它控制字符"))
    if nickname == current_user["username"]:
        return redirect(url_for("index", message="昵称没有变化"))

    ok, error = update_username(current_user["id"], nickname)
    if not ok:
        return redirect(url_for("index", error=error))
    return redirect(url_for("index", message=f"昵称已改为「{nickname}」"))


@app.route("/upload-avatar", methods=["POST"])
def upload_avatar():
    """换「当前用户」的头像：图片存进 static/avatars/，users.avatar 记相对路径。

    前端是身份栏那个小头像：点一下选图片，选完用 fetch 提交到这里，
    返回的 avatar_url 会被直接塞进 <img src>，所以不用刷新整页就能看到新头像。

    限制 / 安全：
        1) 身份只认 session（get_current_user()，没身份直接 403），
           表单里只有 avatar 一个字段，没有 user_id / share_code / u，改不到别人的头像；
        2) 大小不超过 MAX_AVATAR_BYTES（2MB），先看 Content-Length，再按真实字节数复核；
        3) 只收真正的图片（按文件头判断），扩展名由后端决定；
        4) 文件名是「用户id_随机串.扩展名」，不用上传时的原始文件名，
           天然避开 ../ 之类的路径穿越，也顺便解决了浏览器缓存旧头像的问题；
        5) 保存成功后把这个人上一张头像删掉，目录里不会越堆越多。
    返回值（前端 fetch 解析）：
        成功：{"ok": true, "avatar_url": "/static/avatars/1_a1b2c3.jpg"}
        失败：{"ok": false, "error": "人话提示"}
    """
    current_user = get_current_user()

    # 1) 先看请求头里声明的大小：明显超标就不必把整个文件读进内存
    if request.content_length and request.content_length > MAX_AVATAR_BYTES + 64 * 1024:
        return (
            jsonify(ok=False, error="头像不能超过 2MB，请换一张小一点的图片"),
            400,
        )

    avatar = request.files.get("avatar")
    if avatar is None or not avatar.filename:
        return jsonify(ok=False, error="没有收到图片，请重新选择一张头像"), 400

    raw = avatar.read()
    if not raw:
        return jsonify(ok=False, error="这张图片是空的，请重新选择"), 400
    if len(raw) > MAX_AVATAR_BYTES:
        return jsonify(ok=False, error="头像不能超过 2MB，请换一张小一点的图片"), 400

    extension = sniff_image_extension(raw)
    if extension is None:
        return jsonify(ok=False, error="只支持 JPG / PNG / GIF / WebP 图片"), 400

    # 2) 落盘：目录可能在运行期被删掉，这里再建一次
    filename = f"{current_user['id']}_{secrets.token_hex(6)}.{extension}"
    try:
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        (AVATAR_DIR / filename).write_bytes(raw)
    except OSError as exc:
        app.logger.warning("保存头像失败：%s", exc)
        return jsonify(ok=False, error="服务器没能保存这张图片，请稍后再试"), 500

    # 3) 写库；写不进去就把刚存的文件删掉，避免留下没人引用的图片
    relative_path = f"{AVATAR_URL_PREFIX}/{filename}"
    if not update_user_avatar(current_user["id"], relative_path):
        remove_avatar_file(relative_path)
        return jsonify(ok=False, error="头像没能保存，请重新用专属免密链接打开首页后再试"), 500

    # 4) 旧头像已经没人用了，顺手清理（失败也不影响这次上传）
    remove_avatar_file(current_user["avatar"])

    return jsonify(
        ok=True,
        avatar_url=url_for("static", filename=relative_path),
    )


# 导入本文件时就建好表：这样 python app.py 和 flask run 都能直接用
init_db()


if __name__ == "__main__":
    # 云端（VicroCode 等）会通过环境变量 PORT 指定要监听的端口；
    # 本地没有 PORT 这个变量时仍然用 5000。
    port = int(os.environ.get("PORT", 5000))
    # debug 必须写死 False：上线后开 debug 会把源码和交互式调试器暴露给访问者
    app.run(host="0.0.0.0", port=port, debug=False)
