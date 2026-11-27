/* =============================================================
   主题切换脚本（index.html / share.html / no_identity.html 共用）
   -------------------------------------------------------------
   1. 这个文件必须用「不带 defer 的 <script>」放在 <body> 开头：
      浏览器解析到这里时 <body> 已经存在，先按上次的选择贴好
      .theme-endfield 类，再渲染页面内容，刷新时不会闪一下默认主题。
   2. 主题类同时挂在 <body>（主样式）和 <html>（整页底色 / 滚动条兜底）上。
   3. 选择存在 localStorage：'glass' = 清爽毛玻璃（默认），
      'endfield' = 终末地工业风。localStorage 被禁用（隐私模式等）时
      只影响记忆，切换本身照常工作。
   4. 样式全部写在 static/style.css 第 14 节。
   ============================================================= */

(function () {
    'use strict';

    var THEME_KEY = 'class-schedule-theme';   // localStorage 里的键名
    var THEME_GLASS = 'glass';
    var THEME_ENDFIELD = 'endfield';
    var ENDFIELD_CLASS = 'theme-endfield';
    var THEME_COLOR = { glass: '#F7FAFF', endfield: '#121212' };

    // localStorage 在隐私模式 / 老浏览器里可能直接抛错，统一包一层
    function readSavedTheme() {
        try {
            return window.localStorage.getItem(THEME_KEY);
        } catch (err) {
            return null;
        }
    }

    function saveTheme(theme) {
        try {
            window.localStorage.setItem(THEME_KEY, theme);
        } catch (err) {
            // 存不了也没关系，本次切换依然生效
        }
    }

    function isEndfield() {
        return !!(document.body && document.body.classList.contains(ENDFIELD_CLASS));
    }

    // 把按钮状态（aria-pressed / title）同步成当前主题
    function syncThemeToggle(endfield) {
        var button = document.getElementById('theme-toggle');
        if (!button) return;   // 页面里没有按钮（比如脚本提前执行）时什么也不用做

        button.setAttribute('aria-pressed', endfield ? 'true' : 'false');
        button.title = endfield
            ? '当前：终末地工业风 · 点击切换到清爽毛玻璃'
            : '当前：清爽毛玻璃 · 点击切换到终末地工业风';
    }

    // theme：THEME_GLASS / THEME_ENDFIELD；persist：是否写进 localStorage
    function applyTheme(theme, persist) {
        var endfield = theme === THEME_ENDFIELD;

        document.body.classList.toggle(ENDFIELD_CLASS, endfield);
        document.documentElement.classList.toggle(ENDFIELD_CLASS, endfield);

        // 手机上浏览器地址栏 / 状态栏的颜色也跟着换
        var meta = document.getElementById('theme-color-meta');
        if (meta) meta.setAttribute('content', endfield ? THEME_COLOR.endfield : THEME_COLOR.glass);

        syncThemeToggle(endfield);
        if (persist) saveTheme(endfield ? THEME_ENDFIELD : THEME_GLASS);

        return endfield;
    }

    // 供按钮的 onclick 调用（和页面里其他按钮保持同一种写法）
    function toggleTheme() {
        applyTheme(isEndfield() ? THEME_GLASS : THEME_ENDFIELD, true);
    }

    window.toggleTheme = toggleTheme;
    window.applyScheduleTheme = applyTheme;   // 调试用：applyScheduleTheme('endfield', true)

    // 首屏：立刻按上次的选择贴类（内容还没渲染，所以不会闪主题）
    applyTheme(readSavedTheme() === THEME_ENDFIELD ? THEME_ENDFIELD : THEME_GLASS, false);

    // 按钮在页面靠后的位置，DOM 解析完再同步一次状态
    function initThemeToggle() {
        syncThemeToggle(isEndfield());
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initThemeToggle);
    } else {
        initThemeToggle();
    }

    // 同一个浏览器开了多个标签：一个标签切换，其它标签跟着变
    window.addEventListener('storage', function (event) {
        if (event.key !== THEME_KEY) return;
        applyTheme(event.newValue === THEME_ENDFIELD ? THEME_ENDFIELD : THEME_GLASS, false);
    });
})();
