/**
 * Koha-to-DSpace Integration Script (v6.1 Async)
 * Документація: Скрипт додає кнопки архівації/оновлення в інтерфейс Koha.
 */

$(document).ready(function() {
    // --- 1. КОНФІГУРАЦІЯ ---
    const KDV_CONFIG = {
        API_URL: "https://repo.pinokew.buzz/kdv/api",
        REPO_DOMAIN: "repo.pinokew.buzz",
        ROBOT_BATCH_ENDPOINT: "/robot/batch",
        POLLING_INTERVAL: 2000,
        MAX_POLLING_ATTEMPTS: 30, // Захист від нескінченного циклу (1 хвилина)
        ROBOT_MAX_POLLING_ATTEMPTS: 1800, // До 1 години для batch-канарейки
        I18N: {
            updateBtn: "Оновити метадані DSpace",
            archiveBtn: "Архівувати в DSpace",
            confirmArchive: "Архівувати книгу в DSpace? (Фоновий процес)",
            confirmUpdate: "Оновити метадані (Назву, Автора) в DSpace?",
            confirmRobotBatch: "Запустити Robot Batch для вказаного списку?",
            robotBatchBtn: "Запустити Robot Batch",
            success: "✅ Дію завершено успішно!",
            error: "❌ Помилка: ",
            authNeeded: "Потрібна авторизація. Відкрийте вікно, що з'явилося, і повторіть дію."
        }
    };

    const KDV_TOKEN = (window.KDV_TOKEN || "").trim();

    /**
     * Визначає, чи вже є запис у репозиторії
     */
    function detectArchivedRecord() {
        let foundByLink = false;
        const repoDomainLower = KDV_CONFIG.REPO_DOMAIN.toLowerCase();

        $("a[href]").each(function() {
            const href = ($(this).attr("href") || "").trim().toLowerCase();
            if (!href || href.includes("/kdv/api")) return;

            if (
                (repoDomainLower && href.includes(repoDomainLower)) ||
                href.includes("/handle/") ||
                href.includes("/items/")
            ) {
                foundByLink = true;
                return false;
            }
        });

        if (foundByLink) return true;

        const detailsText = $("#catalogue_detail_biblio, .bibliodetails, #details, .results_summary")
            .text()
            .toLowerCase();

        return detailsText && detailsText.includes("856") && (
            detailsText.includes("/handle/") || 
            detailsText.includes("/items/") || 
            (repoDomainLower && detailsText.includes(repoDomainLower))
        );
    }

    function buildHeaders() {
        return KDV_TOKEN ? { "X-KDV-TOKEN": KDV_TOKEN } : {};
    }

    // Точка входу: перевірка сторінки деталей
    if (window.location.href.includes("catalogue/detail.pl")) {
        const urlParams = new URLSearchParams(window.location.search);
        const biblionumber = urlParams.get('biblionumber');
        if (biblionumber) renderIntegrationTools(biblionumber);
    }

    // Точка входу: сторінка результатів пошуку каталогу
    if (window.location.href.includes("catalogue/search.pl")) {
        renderRobotBatchTools();
    }

    function renderRobotBatchTools() {
        if ($("#kdv-robot-batch-panel").length > 0) return;

        const mountPoint = $("#searchresults, #catalogue_search_results, #search_results, .searchresults, #main, main, #doc3")
            .filter(":visible")
            .first();
        const target = mountPoint.length ? mountPoint : $("body");

        const panelHtml = `
            <div id="kdv-robot-batch-panel" class="well well-sm" style="margin: 12px 0; max-width: 860px;">
                <div style="display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; margin-bottom: 8px;">
                    <strong><i class="fa fa-tasks"></i> Robot Batch</strong>
                    <span id="kdv-robot-status" class="text-muted"></span>
                </div>
                <textarea id="kdv-robot-candidates" class="form-control" rows="3" placeholder="100-110&#10;200, 210"></textarea>
                <div style="display: flex; align-items: end; gap: 12px; flex-wrap: wrap; margin-top: 8px;">
                    <label for="kdv-robot-parallelism" style="margin-bottom: 0;">
                        Паралелізм
                        <input type="number" id="kdv-robot-parallelism" class="form-control input-sm" min="1" value="1" style="width: 96px;">
                    </label>
                    <label for="kdv-robot-max-wait" style="margin-bottom: 0;">
                        Очікування, сек
                        <input type="number" id="kdv-robot-max-wait" class="form-control input-sm" min="30" value="900" style="width: 112px;">
                    </label>
                    <label for="kdv-robot-skip-optimization" class="checkbox-inline" style="margin-bottom: 6px;">
                        <input type="checkbox" id="kdv-robot-skip-optimization" name="skip_optimization">
                        Не оптимізовувати файл
                    </label>
                    <button id="kdv-robot-batch-btn" class="btn btn-default btn-sm" type="button">
                        <i class="fa fa-play"></i> ${KDV_CONFIG.I18N.robotBatchBtn}
                    </button>
                </div>
            </div>
        `;

        if (target.is("body")) {
            target.prepend(panelHtml);
        } else {
            target.before(panelHtml);
        }

        $("#kdv-robot-batch-btn").click(function(e) {
            e.preventDefault();
            const candidates = ($("#kdv-robot-candidates").val() || "").trim();
            if (!candidates) {
                alert(KDV_CONFIG.I18N.error + "Не задано candidates");
                return;
            }
            if (!confirm(KDV_CONFIG.I18N.confirmRobotBatch)) return;

            const btn = $(this);
            const originalHtml = btn.html();
            const statusEl = $("#kdv-robot-status");
            btn.prop("disabled", true).html('<i class="fa fa-spinner fa-spin"></i> Обробка...');
            statusEl.text("Старт...");

            ensureAccessSession(() => {
                startRobotBatchRequest(
                    {
                        candidates: candidates,
                        skip_optimization: document.getElementById("kdv-robot-skip-optimization")?.checked ?? false,
                        parallelism: parseInt(document.getElementById("kdv-robot-parallelism")?.value || "1", 10),
                        max_wait: parseInt(document.getElementById("kdv-robot-max-wait")?.value || "900", 10)
                    },
                    (res) => {
                        statusEl.text(`Запущено: ${res.candidates_count} записів`);
                        startRobotPolling(res.task_id, btn, originalHtml, statusEl);
                    },
                    (xhr) => {
                        const msg = xhr.responseJSON?.message || xhr.statusText;
                        statusEl.text("");
                        showError(btn, msg, originalHtml);
                    }
                );
            }, () => {
                statusEl.text("");
                btn.prop("disabled", false).html(originalHtml);
            });
        });
    }

    function startRobotBatchRequest(payload, onSuccess, onError) {
        $.ajax({
            url: `${KDV_CONFIG.API_URL}${KDV_CONFIG.ROBOT_BATCH_ENDPOINT}`,
            type: "POST",
            xhrFields: { withCredentials: true },
            headers: buildHeaders(),
            contentType: "application/json",
            data: JSON.stringify(payload),
            success: onSuccess,
            error: onError
        });
    }

    function renderIntegrationTools(biblionumber) {
        const isArchived = detectArchivedRecord();
        const toolbar = $("#toolbar");
        if (toolbar.length === 0) return;

        const btnConfig = isArchived 
            ? { id: "kdv-update-btn", icon: "fa-refresh", text: KDV_CONFIG.I18N.updateBtn, method: "PUT" }
            : { id: "kdv-integrate-btn", icon: "fa-cloud-upload", text: KDV_CONFIG.I18N.archiveBtn, method: "POST" };

        const skipOptimizationHtml = !isArchived
            ? `
                <label for="kdv-skip-optimization" class="checkbox-inline" style="margin-right: 8px;">
                    <input type="checkbox" id="kdv-skip-optimization" name="skip_optimization">
                    Не оптимізовувати файл
                </label>
            `
            : "";

        const btnHtml = `
            <div class="btn-group">
                ${skipOptimizationHtml}
                <button id="${btnConfig.id}" class="btn btn-default btn-sm" style="${isArchived ? 'color: #007bff; font-weight: bold;' : ''}">
                    <i class="fa ${btnConfig.icon}"></i> ${btnConfig.text}
                </button>
            </div>
        `;
        toolbar.append(btnHtml);

        // Обробник натискання
        $(`#${btnConfig.id}`).click(function(e) {
            e.preventDefault();
            const confirmMsg = isArchived ? KDV_CONFIG.I18N.confirmUpdate : KDV_CONFIG.I18N.confirmArchive;
            if (!confirm(confirmMsg)) return;

            const btn = $(this);
            const originalHtml = btn.html();
            btn.prop("disabled", true).html('<i class="fa fa-spinner fa-spin"></i> Обробка...');

            ensureAccessSession(() => {
                $.ajax({
                    url: `${KDV_CONFIG.API_URL}/integrate/${biblionumber}`,
                    type: btnConfig.method,
                    xhrFields: { withCredentials: true },
                    headers: buildHeaders(),
                    contentType: btnConfig.method === "POST" ? "application/json" : undefined,
                    data: btnConfig.method === "POST" ? JSON.stringify({
                        skip_optimization: document.getElementById("kdv-skip-optimization")?.checked ?? false
                    }) : undefined,
                    success: (res) => {
                        if (btnConfig.method === "POST" && res.task_id) {
                            startPolling(res.task_id, btn, originalHtml);
                        } else {
                            alert(KDV_CONFIG.I18N.success);
                            location.reload();
                        }
                    },
                    error: (xhr) => {
                        const msg = xhr.responseJSON?.message || xhr.statusText;
                        showError(btn, msg, originalHtml);
                    }
                });
            }, () => {
                btn.prop("disabled", false).html(originalHtml);
            });
        });
    }

    function ensureAccessSession(onReady, onFail) {
        $.ajax({
            url: `${KDV_CONFIG.API_URL}/health`,
            type: "GET",
            xhrFields: { withCredentials: true },
            headers: buildHeaders(),
            success: onReady,
            error: () => {
                alert(KDV_CONFIG.I18N.authNeeded);
                window.open(`${KDV_CONFIG.API_URL}/health`, "_blank", "noopener,noreferrer");
                if (onFail) onFail();
            }
        });
    }

    function startRobotPolling(taskId, btn, originalHtml, statusEl) {
        let attempts = 0;
        const pollTimer = setInterval(() => {
            attempts++;
            if (attempts > KDV_CONFIG.ROBOT_MAX_POLLING_ATTEMPTS) {
                clearInterval(pollTimer);
                statusEl.text("");
                showError(btn, "Перевищено час очікування", originalHtml);
                return;
            }

            $.ajax({
                url: `${KDV_CONFIG.API_URL}/status/${taskId}`,
                type: "GET",
                xhrFields: { withCredentials: true },
                headers: buildHeaders(),
                success: (data) => {
                    if (data.status === "queued" || data.status === "processing") {
                        statusEl.text(data.progress || "Обробка...");
                        return;
                    }

                    if (data.status === "success") {
                        clearInterval(pollTimer);
                        const result = data.result || {};
                        const stats = result.stats ? JSON.stringify(result.stats) : "{}";
                        statusEl.text("Завершено");
                        btn.prop("disabled", false).addClass("btn-success").html(originalHtml);
                        alert(`${KDV_CONFIG.I18N.success}
Candidates: ${result.candidates_count || 0}
Stats: ${stats}`);
                        setTimeout(() => btn.removeClass("btn-success"), 3000);
                    } else if (data.status === "error") {
                        clearInterval(pollTimer);
                        statusEl.text("");
                        showError(btn, data.error, originalHtml);
                    }
                },
                error: (xhr) => {
                    if (xhr.status === 404 || xhr.status === 401) {
                        clearInterval(pollTimer);
                        statusEl.text("");
                        showError(btn, `Помилка статусу: ${xhr.status}`, originalHtml);
                    }
                }
            });
        }, KDV_CONFIG.POLLING_INTERVAL);
    }

    function startPolling(taskId, btn, originalHtml) {
        let attempts = 0;
        const pollTimer = setInterval(() => {
            attempts++;
            if (attempts > KDV_CONFIG.MAX_POLLING_ATTEMPTS) {
                clearInterval(pollTimer);
                showError(btn, "Перевищено час очікування", originalHtml);
                return;
            }

            $.ajax({
                url: `${KDV_CONFIG.API_URL}/status/${taskId}`,
                type: "GET",
                xhrFields: { withCredentials: true },
                headers: buildHeaders(),
                success: (data) => {
                    if (data.status === 'success') {
                        clearInterval(pollTimer);
                        btn.addClass("btn-success").html('<i class="fa fa-check"></i>');
                        alert(`${KDV_CONFIG.I18N.success}\nHandle: ${data.result?.handle}`);
                        location.reload();
                    } else if (data.status === 'error') {
                        clearInterval(pollTimer);
                        showError(btn, data.error, originalHtml);
                    }
                },
                error: (xhr) => {
                    if (xhr.status === 404 || xhr.status === 401) {
                        clearInterval(pollTimer);
                        showError(btn, `Помилка статусу: ${xhr.status}`, originalHtml);
                    }
                }
            });
        }, KDV_CONFIG.POLLING_INTERVAL);
    }

    function showError(btn, msg, originalHtml) {
        alert(KDV_CONFIG.I18N.error + msg);
        btn.prop("disabled", false).addClass("btn-danger").html(originalHtml);
        setTimeout(() => btn.removeClass("btn-danger"), 3000);
    }
});