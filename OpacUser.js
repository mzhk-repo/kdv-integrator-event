// Matomo OPAC tracker snippet (managed by koha-deploy IaC patch module)
var _paq = window._paq = window._paq || [];
_paq.push(["disableCookies"]);
_paq.push(['setDoNotTrack', true]);

var _kohaDeviceType = window.matchMedia('(max-width: 767px)').matches ? 'Mobile' : 'Desktop';
_paq.push(['setCustomDimension', 1, _kohaDeviceType]);

function enableSiteSearch(param) {
  try {
    var keyword = new URLSearchParams(window.location.search).get(param);
    if (keyword) {
      _paq.push(['trackSiteSearch', keyword, false, false]);
    }
  } catch (error) {
  }
}

enableSiteSearch('q');

_paq.push(['enableLinkTracking']);
(function() {
  var u="https://matomo.ldubgd.edu.ua/";
  _paq.push(['setTrackerUrl', 'https://matomo.ldubgd.edu.ua/js/ping']);
  _paq.push(['setSiteId', '1']);
  _paq.push(['trackPageView']);
  var d=document, g=d.createElement('script'), s=d.getElementsByTagName('script')[0];
  g.async=true; g.src=u+'matomo.js'; s.parentNode.insertBefore(g,s);
})();

$(document).ready(function() {
    if (window.location.href.includes("opac-detail.pl")) {
        $(".results_summary.description, .description").each(function() {
            var html = $(this).html();
            html = html.replace(/(\b\d+\s*с)(?![\.\wа-яА-ЯіїєґІЇЄҐ])/g, "$1.");
            $(this).html(html);
        });
    }
});

$(document).ready(function () {
    const $facet = $("#copydate_id");

    if (!$facet.length) {
        return;
    }

    const $list = $facet.children("ul");
    const $toggle = $list.children(".moretoggle");

    const items = $list
        .children("li")
        .not(".moretoggle")
        .get();

    items.sort(function (a, b) {
        const yearA = parseInt(
            $(a).find(".facet-label").text().match(/\d{4}/)?.[0] || 0,
            10
        );

        const yearB = parseInt(
            $(b).find(".facet-label").text().match(/\d{4}/)?.[0] || 0,
            10
        );

        return yearB - yearA;
    });

    items.forEach(function (item, index) {
        const $item = $(item);

        // Перші 5 років показуємо,
        // решту залишаємо під "Показати більше"
        if (index < 5) {
            $item
                .removeClass("collapsible-facet")
                .show();
        } else {
            $item
                .addClass("collapsible-facet")
                .hide();
        }

        if ($toggle.length) {
            $toggle.first().before($item);
        } else {
            $list.append($item);
        }
    });
});