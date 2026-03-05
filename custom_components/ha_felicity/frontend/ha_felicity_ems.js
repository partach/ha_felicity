import { LitElement, html, css } from "https://unpkg.com/lit?module";

class FelicityEMSCard extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      config: { type: Object },
      _deviceEntities: { type: Array },
    };
  }

  constructor() {
    super();
    this._deviceEntities = [];
  }

  static getConfigElement() {
    return document.createElement("felicity-ems-card-editor");
  }

  setConfig(config) {
    this.config = {
      currency: "\u20AC",
      ...config,
    };
    this.requestUpdate();
  }

  updated(changedProps) {
    super.updated(changedProps);
    if (changedProps.has("hass")) {
      this._resolveDeviceEntities();
      this._drawSlotTimeline();
    }
  }

  _resolveDeviceEntities() {
    if (!this.hass || !this.config.device_id) return;
    const entityReg = this.hass.entities;
    if (!entityReg) return;
    this._deviceEntities = Object.values(entityReg)
      .filter((e) => e.device_id === this.config.device_id)
      .map((e) => e.entity_id)
      .sort();
  }

  _getEntityId(key) {
    if (!this._deviceEntities?.length) return null;
    return this._deviceEntities.find((eid) => eid.endsWith(`_${key}`));
  }

  _getState(key) {
    const eid = this._getEntityId(key);
    if (!eid) return null;
    const entity = this.hass.states[eid];
    if (!entity || entity.state === "unknown" || entity.state === "unavailable") return null;
    return entity.state;
  }

  _getNumericState(key) {
    const val = this._getState(key);
    if (val == null) return null;
    const num = parseFloat(val);
    return isNaN(num) ? null : num;
  }

  _getAttr(key, attr) {
    const eid = this._getEntityId(key);
    if (!eid) return undefined;
    const entity = this.hass.states[eid];
    if (!entity) return undefined;
    return entity.attributes?.[attr];
  }

  // Helper: safely format a number, returns fallback string if value is null/undefined
  _fmt(val, decimals, fallback = "\u2014") {
    if (val == null || (typeof val === "number" && isNaN(val))) return fallback;
    return Number(val).toFixed(decimals);
  }

  // ── Slot Timeline (Canvas) ──────────────────────────────────

  _drawSlotTimeline() {
    const canvas = this.shadowRoot?.querySelector("#slot-timeline");
    if (!canvas) return;

    const slotData = this._getAttr("schedule_status", "slot_schedule");
    const threshold = this._getNumericState("price_threshold");
    const currentPrice = this._getNumericState("current_price");
    const granularity = this._getAttr("schedule_status", "slot_granularity_min") || 60;

    if (!slotData || !slotData.length) {
      const ctx = canvas.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      canvas.width = canvas.offsetWidth * dpr;
      canvas.height = canvas.offsetHeight * dpr;
      ctx.scale(dpr, dpr);
      ctx.fillStyle = getComputedStyle(this).getPropertyValue("--secondary-text-color") || "#888";
      ctx.font = "12px sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("No slot data available", canvas.offsetWidth / 2, canvas.offsetHeight / 2);
      return;
    }

    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.offsetWidth;
    const h = canvas.offsetHeight;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.scale(dpr, dpr);

    const numSlots = slotData.length;
    const barW = Math.max(1, (w - 40) / numSlots); // left margin 30, right 10
    const marginLeft = 30;
    const marginTop = 15;
    const marginBottom = 20;
    const chartH = h - marginTop - marginBottom;

    // Find price range
    const prices = slotData.map((s) => s.price).filter((p) => p != null);
    const minPrice = Math.min(...prices, 0);
    const maxPrice = Math.max(...prices, 0.01);
    const range = maxPrice - minPrice || 0.01;

    // Current time marker
    const now = new Date();
    const currentSlot = Math.floor((now.getHours() * 60 + now.getMinutes()) / granularity);

    // Draw bars
    for (let i = 0; i < numSlots; i++) {
      const slot = slotData[i];
      const x = marginLeft + i * barW;
      const price = slot.price ?? 0;
      const barH = ((price - minPrice) / range) * chartH;
      const y = marginTop + chartH - barH;

      // Color based on action
      if (slot.action === "charge") {
        ctx.fillStyle = "#4CAF50"; // green
      } else if (slot.action === "discharge") {
        ctx.fillStyle = "#FF9800"; // orange
      } else if (price < 0) {
        ctx.fillStyle = "#2196F3"; // blue for negative
      } else {
        ctx.fillStyle = "rgba(150, 150, 150, 0.4)"; // grey idle
      }

      // Current slot highlight
      if (i === currentSlot) {
        ctx.fillStyle = slot.action === "charge" ? "#66BB6A"
          : slot.action === "discharge" ? "#FFB74D"
          : "#BBDEFB";
      }

      ctx.fillRect(x + 0.5, y, Math.max(1, barW - 1), barH);

      // Current slot border
      if (i === currentSlot) {
        ctx.strokeStyle = "#FFF";
        ctx.lineWidth = 2;
        ctx.strokeRect(x, marginTop, barW, chartH);
      }
    }

    // Threshold line
    if (threshold != null && threshold >= minPrice && threshold <= maxPrice) {
      const thresholdY = marginTop + chartH - ((threshold - minPrice) / range) * chartH;
      ctx.strokeStyle = "#F44336";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(marginLeft, thresholdY);
      ctx.lineTo(w - 10, thresholdY);
      ctx.stroke();
      ctx.setLineDash([]);

      // Label
      ctx.fillStyle = "#F44336";
      ctx.font = "9px sans-serif";
      ctx.textAlign = "right";
      ctx.fillText(`${threshold.toFixed(2)}`, marginLeft - 2, thresholdY + 3);
    }

    // Zero line (if negative prices exist)
    if (minPrice < 0) {
      const zeroY = marginTop + chartH - ((0 - minPrice) / range) * chartH;
      ctx.strokeStyle = "rgba(255,255,255,0.3)";
      ctx.lineWidth = 0.5;
      ctx.beginPath();
      ctx.moveTo(marginLeft, zeroY);
      ctx.lineTo(w - 10, zeroY);
      ctx.stroke();
    }

    // Y-axis labels
    ctx.fillStyle = getComputedStyle(this).getPropertyValue("--secondary-text-color") || "#aaa";
    ctx.font = "9px sans-serif";
    ctx.textAlign = "right";
    ctx.fillText(maxPrice.toFixed(2), marginLeft - 2, marginTop + 8);
    ctx.fillText(minPrice.toFixed(2), marginLeft - 2, marginTop + chartH);

    // X-axis hour labels
    ctx.textAlign = "center";
    ctx.fillStyle = getComputedStyle(this).getPropertyValue("--secondary-text-color") || "#aaa";
    const slotsPerHour = 60 / granularity;
    const labelInterval = granularity <= 15 ? 4 : granularity <= 30 ? 2 : 1; // every 2-4 hours for dense slots
    const hourLabelEvery = Math.max(1, Math.ceil(3 / (slotsPerHour * labelInterval)));
    for (let hour = 0; hour < 24; hour += hourLabelEvery) {
      const slotIdx = hour * slotsPerHour;
      if (slotIdx < numSlots) {
        const x = marginLeft + slotIdx * barW + barW / 2;
        ctx.fillText(`${hour}`, x, h - 3);
      }
    }

    // Legend at top-right
    ctx.font = "9px sans-serif";
    ctx.textAlign = "right";
    const legendX = w - 5;
    ctx.fillStyle = "#4CAF50";
    ctx.fillRect(legendX - 55, 2, 8, 8);
    ctx.fillStyle = getComputedStyle(this).getPropertyValue("--primary-text-color") || "#fff";
    ctx.fillText("charge", legendX - 58 + 55, 9);
    ctx.fillStyle = "#FF9800";
    ctx.fillRect(legendX - 115, 2, 8, 8);
    ctx.fillStyle = getComputedStyle(this).getPropertyValue("--primary-text-color") || "#fff";
    ctx.textAlign = "right";
    ctx.fillText("sell", legendX - 118 + 55, 9);
  }

  // ── Service calls for controls ──────────────────────────────

  async _setSelect(key, option) {
    const eid = this._getEntityId(key);
    if (!eid) return;
    try {
      await this.hass.callService("select", "select_option", {
        entity_id: eid,
        option: option,
      });
    } catch (err) {
      console.error(`Failed to set ${key}:`, err);
    }
  }

  async _setNumber(key, value) {
    const eid = this._getEntityId(key);
    if (!eid) return;
    try {
      await this.hass.callService("number", "set_value", {
        entity_id: eid,
        value: parseFloat(value),
      });
    } catch (err) {
      console.error(`Failed to set ${key}:`, err);
    }
  }

  // ── Render ──────────────────────────────────────────────────

  render() {
    if (!this.hass || !this.config) {
      return html`<ha-card><div class="card-content">Loading...</div></ha-card>`;
    }

    const energyState = this._getState("energy_state") || "unknown";
    const scheduleStatus = this._getState("schedule_status") || "unknown";
    const currentPrice = this._getNumericState("current_price");
    const threshold = this._getNumericState("price_threshold");
    const gridMode = this._getState("grid_mode") || "off";
    const priceMode = this._getState("price_mode") || "manual";
    const likelihood = this._getState("charge_likelihood") || "unknown";
    const currency = this.config.currency || "\u20AC";

    // Schedule info from attributes (use _fmt for safe formatting)
    const chargeSlots = this._getAttr("schedule_status", "scheduled_charge_slots") || 0;
    const dischargeSlots = this._getAttr("schedule_status", "scheduled_discharge_slots") || 0;
    const gridPlanned = this._getAttr("schedule_status", "grid_energy_planned_kwh");
    const pvRemaining = this._getNumericState("pv_forecast_remaining");
    const pvToday = this._getNumericState("pv_forecast_today");
    const pvTomorrow = this._getNumericState("pv_forecast_tomorrow");
    const reserve = this._getAttr("energy_state", "self_consumption_reserve");
    const weeklyConsumption = this._getNumericState("weekly_avg_consumption");
    const safeMaxPower = this._getNumericState("safe_max_power");

    return html`
      <ha-card>
        <div class="card-header">
          <div class="name">${this.config.name || "Energy Management"}</div>
          <div class="status-badges">
            <span class="badge ${energyState}">${energyState}</span>
            <span class="badge schedule-${scheduleStatus}">${scheduleStatus}</span>
          </div>
        </div>

        <div class="card-content">
          <!-- Price & State summary -->
          <div class="summary-row">
            <div class="summary-item">
              <span class="label">Price</span>
              <span class="value price">${this._fmt(currentPrice, 3)} ${currency}/kWh</span>
            </div>
            <div class="summary-item">
              <span class="label">Threshold</span>
              <span class="value">${this._fmt(threshold, 3)} ${currency}/kWh</span>
            </div>
            <div class="summary-item">
              <span class="label">Likelihood</span>
              <span class="value likelihood-${likelihood}">${likelihood}</span>
            </div>
          </div>

          <!-- Slot Timeline -->
          <div class="timeline-container">
            <div class="timeline-label">Today's Schedule</div>
            <canvas id="slot-timeline"></canvas>
          </div>

          <!-- Schedule stats -->
          <div class="stats-row">
            <div class="stat">
              <ha-icon icon="mdi:battery-charging"></ha-icon>
              <span>${chargeSlots} charge</span>
            </div>
            <div class="stat">
              <ha-icon icon="mdi:transmission-tower-export"></ha-icon>
              <span>${dischargeSlots} sell</span>
            </div>
            <div class="stat">
              <ha-icon icon="mdi:lightning-bolt"></ha-icon>
              <span>${this._fmt(gridPlanned, 1)} kWh planned</span>
            </div>
            <div class="stat">
              <ha-icon icon="mdi:shield-sun"></ha-icon>
              <span>${this._fmt(reserve, 1)} kWh reserve</span>
            </div>
          </div>

          <!-- PV Forecast -->
          <div class="pv-row">
            <div class="pv-item">
              <ha-icon icon="mdi:solar-power"></ha-icon>
              <div>
                <span class="pv-label">PV Remaining</span>
                <span class="pv-value">${this._fmt(pvRemaining, 1)} kWh</span>
              </div>
            </div>
            <div class="pv-item">
              <ha-icon icon="mdi:weather-sunny"></ha-icon>
              <div>
                <span class="pv-label">Today Total</span>
                <span class="pv-value">${this._fmt(pvToday, 1)} kWh</span>
              </div>
            </div>
            <div class="pv-item">
              <ha-icon icon="mdi:weather-sunny-alert"></ha-icon>
              <div>
                <span class="pv-label">Tomorrow</span>
                <span class="pv-value">${this._fmt(pvTomorrow, 1)} kWh</span>
              </div>
            </div>
          </div>

          <!-- Controls -->
          <div class="controls-section">
            <div class="controls-label">Controls</div>
            <div class="controls-grid">
              ${this._renderGridModeControl(gridMode)}
              ${this._renderPriceModeControl(priceMode)}
              ${this._renderPowerLevelControl()}
              ${this._renderPriceThresholdControl()}
            </div>
          </div>

          <!-- Info footer -->
          <div class="info-footer">
            ${weeklyConsumption != null ? html`<span>Avg consumption: ${this._fmt(weeklyConsumption, 1)} kWh/day</span>` : ""}
            ${safeMaxPower != null ? html`<span>Safe power: ${this._fmt(safeMaxPower / 1000, 1)} kW</span>` : ""}
          </div>
        </div>
      </ha-card>
    `;
  }

  _renderGridModeControl(current) {
    const options = ["off", "from_grid", "to_grid", "both"];
    return html`
      <div class="control-item">
        <span class="control-label">Grid Mode</span>
        <select @change=${(e) => this._setSelect("grid_mode", e.target.value)}>
          ${options.map((o) => html`<option value="${o}" ?selected=${o === current}>${o}</option>`)}
        </select>
      </div>
    `;
  }

  _renderPriceModeControl(current) {
    const options = ["manual", "auto"];
    return html`
      <div class="control-item">
        <span class="control-label">Price Mode</span>
        <select @change=${(e) => this._setSelect("price_mode", e.target.value)}>
          ${options.map((o) => html`<option value="${o}" ?selected=${o === current}>${o}</option>`)}
        </select>
      </div>
    `;
  }

  _renderPowerLevelControl() {
    const eid = this._getEntityId("power_level");
    if (!eid) return html``;
    const entity = this.hass.states[eid];
    if (!entity) return html``;
    const current = parseFloat(entity.state) || 5;

    return html`
      <div class="control-item">
        <span class="control-label">Power ${current} kW</span>
        <input type="range" min="1" max="10" step="0.5" .value=${current}
          @change=${(e) => this._setNumber("power_level", e.target.value)} />
      </div>
    `;
  }

  _renderPriceThresholdControl() {
    const eid = this._getEntityId("price_threshold_level");
    if (!eid) return html``;
    const entity = this.hass.states[eid];
    if (!entity) return html``;
    const current = parseInt(entity.state) || 5;

    return html`
      <div class="control-item">
        <span class="control-label">Price Level ${current}/10</span>
        <input type="range" min="1" max="10" step="1" .value=${current}
          @change=${(e) => this._setNumber("price_threshold_level", e.target.value)} />
      </div>
    `;
  }

  // ── Styles ──────────────────────────────────────────────────

  static get styles() {
    return css`
      ha-card {
        padding: 0;
        overflow: hidden;
      }
      .card-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 12px 16px 8px;
        border-bottom: 1px solid var(--divider-color);
      }
      .card-header .name {
        font-size: 1.2em;
        font-weight: 500;
      }
      .status-badges {
        display: flex;
        gap: 6px;
      }
      .badge {
        font-size: 0.75em;
        padding: 2px 8px;
        border-radius: 10px;
        text-transform: uppercase;
        font-weight: 600;
        letter-spacing: 0.5px;
      }
      .badge.charging { background: #4CAF50; color: #fff; }
      .badge.discharging { background: #FF9800; color: #fff; }
      .badge.idle { background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .badge.unknown { background: var(--secondary-background-color); color: var(--secondary-text-color); }
      .badge.schedule-active { background: #2196F3; color: #fff; }
      .badge.schedule-waiting { background: #607D8B; color: #fff; }
      .badge.schedule-manual { background: #9C27B0; color: #fff; }
      .badge.schedule-off { background: var(--secondary-background-color); color: var(--secondary-text-color); }

      .card-content {
        padding: 12px 16px 16px;
      }

      /* Summary row */
      .summary-row {
        display: flex;
        justify-content: space-between;
        margin-bottom: 12px;
      }
      .summary-item {
        display: flex;
        flex-direction: column;
        align-items: center;
      }
      .summary-item .label {
        font-size: 0.75em;
        color: var(--secondary-text-color);
        text-transform: uppercase;
      }
      .summary-item .value {
        font-size: 0.95em;
        font-weight: 500;
      }
      .value.price { color: #FFD600; }
      .likelihood-on_track { color: #4CAF50; }
      .likelihood-tight { color: #FF9800; }
      .likelihood-at_risk { color: #F44336; }
      .likelihood-insufficient { color: #F44336; font-weight: 700; }

      /* Timeline */
      .timeline-container {
        margin-bottom: 12px;
      }
      .timeline-label {
        font-size: 0.8em;
        color: var(--secondary-text-color);
        margin-bottom: 4px;
      }
      #slot-timeline {
        width: 100%;
        height: 120px;
        border-radius: 6px;
        background: var(--secondary-background-color);
      }

      /* Stats row */
      .stats-row {
        display: flex;
        justify-content: space-around;
        margin-bottom: 12px;
        flex-wrap: wrap;
        gap: 4px;
      }
      .stat {
        display: flex;
        align-items: center;
        gap: 4px;
        font-size: 0.8em;
        color: var(--secondary-text-color);
      }
      .stat ha-icon {
        --mdc-icon-size: 16px;
        color: var(--secondary-text-color);
      }

      /* PV row */
      .pv-row {
        display: flex;
        justify-content: space-around;
        margin-bottom: 14px;
        padding: 8px;
        border-radius: 8px;
        background: var(--secondary-background-color);
      }
      .pv-item {
        display: flex;
        align-items: center;
        gap: 6px;
      }
      .pv-item ha-icon {
        --mdc-icon-size: 20px;
        color: #FFD600;
      }
      .pv-label {
        display: block;
        font-size: 0.7em;
        color: var(--secondary-text-color);
      }
      .pv-value {
        display: block;
        font-size: 0.9em;
        font-weight: 500;
      }

      /* Controls */
      .controls-section {
        border-top: 1px solid var(--divider-color);
        padding-top: 10px;
        margin-bottom: 8px;
      }
      .controls-label {
        font-size: 0.8em;
        color: var(--secondary-text-color);
        margin-bottom: 8px;
        text-transform: uppercase;
      }
      .controls-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 8px;
      }
      .control-item {
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .control-label {
        font-size: 0.75em;
        color: var(--secondary-text-color);
      }
      .control-item select,
      .control-item input[type="range"] {
        width: 100%;
        padding: 4px;
        border: 1px solid var(--divider-color);
        border-radius: 4px;
        background: var(--secondary-background-color);
        color: var(--primary-text-color);
        font-size: 0.85em;
      }
      .control-item input[type="range"] {
        padding: 0;
        height: 24px;
        accent-color: var(--primary-color);
      }

      /* Info footer */
      .info-footer {
        display: flex;
        justify-content: space-between;
        font-size: 0.7em;
        color: var(--secondary-text-color);
        padding-top: 6px;
        border-top: 1px solid var(--divider-color);
      }
    `;
  }
}

// ── Editor ──────────────────────────────────────────────────

class FelicityEMSCardEditor extends LitElement {
  static get properties() {
    return {
      hass: {},
      _config: {},
    };
  }

  setConfig(config) {
    this._config = config;
  }

  render() {
    if (!this.hass || !this._config) return html``;

    return html`
      <div class="editor">
        <ha-textfield
          label="Card Name"
          .value=${this._config.name || ""}
          @change=${(e) => this._valueChanged("name", e.target.value)}
        ></ha-textfield>

        <ha-selector
          .hass=${this.hass}
          .selector=${{ device: { integration: "ha_felicity" } }}
          .value=${this._config.device_id || ""}
          .label=${"Device"}
          @value-changed=${(e) => this._valueChanged("device_id", e.detail.value)}
        ></ha-selector>

        <ha-textfield
          label="Currency Symbol"
          .value=${this._config.currency || "\u20AC"}
          @change=${(e) => this._valueChanged("currency", e.target.value)}
        ></ha-textfield>
      </div>
    `;
  }

  _valueChanged(field, value) {
    const newConfig = { ...this._config, [field]: value };
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: newConfig },
        bubbles: true,
        composed: true,
      })
    );
  }

  static get styles() {
    return css`
      .editor {
        display: flex;
        flex-direction: column;
        gap: 12px;
        padding: 16px;
      }
    `;
  }
}

customElements.define("felicity-ems-card", FelicityEMSCard);
customElements.define("felicity-ems-card-editor", FelicityEMSCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "felicity-ems-card",
  name: "Felicity EMS Card",
  description: "Energy Management System \u2014 slot timeline, schedule, and controls",
  preview: true,
});
