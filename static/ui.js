/* =============================================================
   课程卡片配色脚本（index.html / share.html 共用）
   -------------------------------------------------------------
   规则：按「课程名」算一个稳定的编号，给卡片加上 tone-1 ~ tone-6。
   同一门课在任何页面、任何刷新之后都是同一种柔和颜色，
   不依赖随机数，所以两个人看到的颜色也一致。
   具体的色值写在 static/style.css 的 .course.tone-* 里。
   ============================================================= */

(function () {
    'use strict';

    var TONE_COUNT = 6;   // 和 style.css 里的 tone 组数保持一致

    // 简单的字符串哈希：同名 → 同一个数
    function hashName(name) {
        var hash = 0;
        for (var i = 0; i < name.length; i++) {
            hash = (hash * 31 + name.charCodeAt(i)) % 1000003;
        }
        return hash;
    }

    function applyCourseTones() {
        var cards = document.querySelectorAll('.course');

        for (var i = 0; i < cards.length; i++) {
            var card = cards[i];
            // 模板里的 data-name 就是课程名；老页面没有这个属性时退回用文字内容
            var name = (card.dataset.name || card.textContent || '').trim();
            if (!name) continue;

            // 先清掉旧配色，重复调用也不会叠加
            for (var tone = 1; tone <= TONE_COUNT; tone++) {
                card.classList.remove('tone-' + tone);
            }
            card.classList.add('tone-' + (hashName(name) % TONE_COUNT + 1));
        }
    }

    // 用 defer 引入时这个判断通常已经是 interactive，
    // 但两种写法都留着，直接内联调用也不会出错。
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', applyCourseTones);
    } else {
        applyCourseTones();
    }
})();
