(() => {
  "use strict";

  const profileDefinitions = {
    "dyn-iv113": [
      {
        id: "dyn-iv113",
        label: "Baseline",
        detail: "Текущий paper-профиль · target vol 70%",
      },
      {
        id: "dyn-iv113-risk50",
        label: "Risk 50%",
        detail: "Shadow · target vol 50%",
      },
      {
        id: "dyn-iv113-band2",
        label: "Band 2%",
        detail: "Shadow · deadband 2 п.п.",
      },
    ],
    "atlas-nx": [
      {
        id: "atlas-nx",
        label: "Atlas NX R1",
        detail: "Активная identity · собственный forward clock",
      },
      {
        id: "atlas-v517-reference",
        label: "V517 reference",
        detail: "Frozen predecessor · метрики не принадлежат Atlas NX R1",
      },
    ],
  };

  const selectedProfiles = new Map();
  let currentBaseStrategyId = null;
  const originalFetch = window.fetch.bind(window);

  const profileSection = () => document.querySelector("#backtest-profile-section");
  const profileContainer = () => document.querySelector("#backtest-profiles");
  const profileNote = () => document.querySelector("#backtest-profile-note");
  const backtestButton = () => document.querySelector("#backtest-button");

  const selectedProfileId = (baseStrategyId) =>
    selectedProfiles.get(baseStrategyId) || baseStrategyId;

  const queueBacktestRun = () => {
    const trigger = () => {
      const button = backtestButton();
      if (!button) return;
      if (button.disabled) {
        window.setTimeout(trigger, 50);
        return;
      }
      button.click();
    };
    window.setTimeout(trigger, 0);
  };

  const renderProfiles = (baseStrategyId) => {
    const section = profileSection();
    const container = profileContainer();
    const note = profileNote();
    if (!section || !container || !note) return;

    const definitions = profileDefinitions[baseStrategyId] || [];
    section.hidden = definitions.length < 2;
    container.replaceChildren();
    if (definitions.length < 2) {
      note.textContent = "";
      return;
    }

    const activeId = selectedProfileId(baseStrategyId);
    const active = definitions.find((item) => item.id === activeId) || definitions[0];
    note.textContent = active.detail;
    const loading = Boolean(backtestButton()?.disabled);

    for (const definition of definitions) {
      const button = document.createElement("button");
      button.type = "button";
      button.disabled = loading;
      button.className = `backtest-profile${definition.id === active.id ? " active" : ""}`;
      button.setAttribute("role", "tab");
      button.setAttribute(
        "aria-selected",
        definition.id === active.id ? "true" : "false"
      );
      button.dataset.backtestProfileId = definition.id;

      const label = document.createElement("strong");
      label.textContent = definition.label;
      const detail = document.createElement("small");
      detail.textContent = definition.detail;
      button.append(label, detail);

      button.addEventListener("click", () => {
        if (definition.id === selectedProfileId(baseStrategyId)) return;
        selectedProfiles.set(baseStrategyId, definition.id);
        renderProfiles(baseStrategyId);
        queueBacktestRun();
      });
      container.append(button);
    }
  };

  const rewriteBacktestResponse = async (response, baseStrategyId, requestedId) => {
    if (!response.ok || requestedId === baseStrategyId) return response;
    const payload = await response.clone().json();
    if (!payload || typeof payload !== "object") return response;
    payload.frontend_requested_strategy_id = requestedId;
    payload.strategy_id = baseStrategyId;
    const headers = new Headers(response.headers);
    headers.delete("content-length");
    headers.set("content-type", "application/json; charset=utf-8");
    return new Response(JSON.stringify(payload), {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  };

  window.fetch = async (input, init) => {
    const rawUrl = typeof input === "string" ? input : input?.url;
    if (!rawUrl) return originalFetch(input, init);

    const url = new URL(rawUrl, window.location.href);
    const prefix = "/api/v1/backtests/";
    if (!url.pathname.startsWith(prefix)) return originalFetch(input, init);

    const baseStrategyId = decodeURIComponent(url.pathname.slice(prefix.length));
    currentBaseStrategyId = baseStrategyId;
    renderProfiles(baseStrategyId);
    if (!profileDefinitions[baseStrategyId]) return originalFetch(input, init);

    const requestedId = selectedProfileId(baseStrategyId);
    url.pathname = `${prefix}${encodeURIComponent(requestedId)}`;
    const requestUrl =
      url.origin === window.location.origin
        ? `${url.pathname}${url.search}${url.hash}`
        : url.toString();
    const response = await originalFetch(requestUrl, init);
    return rewriteBacktestResponse(response, baseStrategyId, requestedId);
  };

  document.addEventListener("click", (event) => {
    if (event.target?.closest?.("#backtest-dialog-close")) {
      currentBaseStrategyId = null;
    }
  });

  const button = backtestButton();
  if (button) {
    new MutationObserver(() => {
      if (currentBaseStrategyId) renderProfiles(currentBaseStrategyId);
    }).observe(button, { attributes: true, attributeFilter: ["disabled"] });
  }
})();
