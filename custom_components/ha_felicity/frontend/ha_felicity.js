// felicity-inverter-card.js
import { LitElement, html, css } from "https://unpkg.com/lit?module";

class FelicityInverterCard extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      config: { type: Object },
      _selectedRuleIndex: { type: Number },
      _selectedEntity: { type: String },
      _selectedKeyEntity: { type: String },
      _writeValue: { type: String },
      _allEntities: { type: Array },
      _keyOptions: { type: Array },
      _selectedStatus: { type: String },
      _selectedSection: { type: String },
      _entityPrefix: { type: String },
    };
  }

  constructor() {
    super();
    this._selectedRuleIndex = 0;
    this._selectedEntity = "";
    this._selectedKeyEntity = "";
    this._writeValue = "";
    this._allEntities = [];
    this._keyOptions = [];
    this._selectedSection = "energy_flow"; // Default to energy flow
    this._entityPrefix = "felicity_inverter";
  }

  setConfig(config) {
    if (!config.entities && !config.econ_rules) {
      throw new Error("You must define 'entities' or 'econ_rules'");
    }
    this.config = config;
  }

  getCardSize() {
    return 10;
  }

  updated(changedProps) {
    if (changedProps.has("hass")) {
      this._resolveEntityPrefix();
      this._updateEntitiesAndKeys();
    }
  }

  _resolveEntityPrefix() {
    if (!this.config.device_id || !this.hass?.devices) return;

    const device = this.hass.devices[this.config.device_id];
    if (!device) return;

    const entityId = device.entities?.[0];
    if (!entityId) return;

    const parts = entityId.split(".");
    if (parts.length >= 2) {
      this._entityPrefix = parts[1];
    }
  }
  _updateEntitiesAndKeys() {
    this._allEntities = Object.keys(this.hass.states)
      .filter(eid => eid.includes(this._entityPrefix))
      .sort();
  }

  render() {
    if (!this.hass || !this.config) return html``;

    const selectedRule = this.config.econ_rules?.[this._selectedRuleIndex];
    const ruleStateObj = selectedRule ? this.hass.states[selectedRule.entity_id] : null;
    const ruleAttrs = ruleStateObj?.attributes || {};

    return html`
      <ha-card>
        <div class="header">${this.config.name || "Felicity Inverter"}</div>

        <!-- Section Selector -->
        <div class="section-selector">
          <select @change=${e => this._selectedSection = e.target.value}>
            <option value="energy_flow" ?selected=${this._selectedSection === "energy_flow"}>
              Energy Flow
            </option>
            ${this.config.entities?.length > 0 ? html`
              <option value="sensors" ?selected=${this._selectedSection === "sensors"}>
                Main Sensors
              </option>
            ` : ""}
            ${this.config.econ_rules?.length > 0 ? html`
              <option value="econ_rules" ?selected=${this._selectedSection === "econ_rules"}>
                Economic Rules
              </option>
            ` : ""}
            <option value="write_register" ?selected=${this._selectedSection === "write_register"}>
              Write Register
            </option>
          </select>
        </div>

        <!-- SECTION 1: Energy Flow -->
        ${this._selectedSection === "energy_flow" ? html`
          <div class="section">
            <div class="flow-diagram">
              <style>
                .flow-diagram {
                  position: relative;
                  height: 250px;
                  margin: 20px 0;
                }
                
                .flow-item {
                  position: absolute;
                  text-align: center;
                  display: flex;
                  flex-direction: column;
                  align-items: center;
                  gap: 4px;
                  z-index: 2;
                }
                
                .flow-item ha-icon {
                  font-size: 40px;
                }
                
                .power-value {
                  font-size: 1.2em;
                  font-weight: bold;
                  color: var(--primary-color, #03a9f4);
                }
                
                .soc {
                  font-size: 1.2em;
                  font-weight: bold;
                  color: var(--success-color, #4caf50);
                }
                
                .label {
                  font-size: 0.85em;
                  color: var(--secondary-text-color);
                }
                
                .pv { 
                  top: 15px; 
                  left: 15%; 
                  transform: translateX(-50%);
                  gap: 2px;
                }
                
                .grid { 
                  top: 15px; 
                  right: 15%; 
                  transform: translateX(50%);
                  gap: 2px;
                }
                
                .inverter { 
                  top: 40%; 
                  left: 50%; 
                  transform: translate(-50%, -50%);
                  flex-direction: column-reverse;
                  gap: 4px;
                }
                
                .battery { 
                  bottom: 15px; 
                  left: 15%; 
                  transform: translateX(-50%);
                  flex-direction: row;
                  align-items: center;
                  gap: 8px;
                }
                
                .battery-info {
                  display: flex;
                  flex-direction: column;
                  gap: 2px;
                  text-align: left;
                }
                
                .home { 
                  bottom: 15px; 
                  right: 15%; 
                  transform: translateX(50%);
                }
                
                .backup { 
                  bottom: 15px; 
                  left: 50%; 
                  transform: translateX(-50%);
                }
                
                .inverter ha-icon {
                  font-size: 50px !important;
                  color: orange;
                }
                
                svg.flow-svg {
                  position: absolute;
                  top: 0;
                  left: 0;
                  width: 100%;
                  height: 100%;
                  pointer-events: none;
                  z-index: 1;
                }
                
                .flow-path {
                  fill: none;
                  stroke: var(--primary-color, #03a9f4);
                  stroke-width: 3;
                  opacity: 0.6;
                }
                
                .flow-path.active {
                  opacity: 1;
                  stroke-dasharray: 10 5;
                  animation: flow 1.5s linear infinite;
                }
                
                .flow-path.active.reverse {
                  animation: flow-reverse 1.5s linear infinite;
                }
                
                .flow-path.charging { stroke: var(--success-color, #4caf50); }
                .flow-path.discharging { stroke: var(--warning-color, #ff9800); }
                .flow-path.grid-import { stroke: var(--error-color, #f44336); }
                .flow-path.grid-export { stroke: var(--success-color, #8bc34a); }
                .flow-path.inactive { opacity: 0.2; }
                
                @keyframes flow {
                  to { stroke-dashoffset: -15; }
                }
                
                @keyframes flow-reverse {
                  to { stroke-dashoffset: 15; }
                }
              </style>

              <svg class="flow-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
                <path 
                  class="flow-path ${this._getPower('pv_total_power') > 50 ? 'active' : 'inactive'}" 
                  d="M 20 22 L 47 48" 
                  vector-effect="non-scaling-stroke"
                />
                <path 
                  class="flow-path ${Math.abs(this._getPower('ac_input_power')) > 50 ? 'active ' + (this._getPower('ac_input_power') > 0 ? 'grid-import' : 'grid-export reverse') : 'inactive'}" 
                  d="M 80 22 L 53 48" 
                  vector-effect="non-scaling-stroke"
                />
                <path 
                  class="flow-path ${Math.abs(this._getPower('battery_power')) > 50 ? 'active ' + (this._getPower('battery_power') > 0 ? 'charging' : 'discharging reverse') : 'inactive'}" 
                  d="M 47 52 L 20 78" 
                  vector-effect="non-scaling-stroke"
                />
                <path 
                  class="flow-path ${this._getPower('ac_output_active_power') > 50 ? 'active' : 'inactive'}" 
                  d="M 53 52 L 80 78" 
                  vector-effect="non-scaling-stroke"
                />
                <path 
                  class="flow-path ${this._getPower('backup_load') > 50 ? 'active' : 'inactive'}" 
                  d="M 50 52 L 50 78" 
                  vector-effect="non-scaling-stroke"
                />
              </svg>

              <div class="flow-item pv">
                <ha-icon icon="mdi:solar-panel-large"></ha-icon>
                <div class="power-value">${this._getPower("pv_total_power")} W</div>
                <div class="label">PV</div>
              </div>

              <div class="flow-item grid">
                <ha-icon icon="mdi:transmission-tower"></ha-icon>
                <div class="power-value">${this._getPower("ac_input_power")} W</div>
                <div class="label">Grid</div>
              </div>

              <div class="flow-item inverter">
                <ha-icon icon="mdi:lightning-bolt"></ha-icon>
                <div class="label">Inverter</div>
              </div>

              <div class="flow-item battery">
                <ha-icon icon="mdi:battery${this._getBatteryIcon()}"></ha-icon>
                <div class="battery-info">
                  <div class="soc">${this._getValue("battery_capacity") ?? "—"} %</div>
                  <div class="power-value">${Math.abs(this._getPower("battery_power"))} W</div>
                  <div class="label">${this._getBatteryState()}</div>
                </div>
              </div>

              <div class="flow-item home">
                <ha-icon icon="mdi:home"></ha-icon>
                <div class="power-value">${this._getPower("ac_output_active_power")} W</div>
                <div class="label">Home Load</div>
              </div>

              <div class="flow-item backup">
                <div class="power-value">${this._getPower("backup_load") || 0} W</div>
                <div class="label">Backup Load</div>
              </div>
            </div>
          </div>
        ` : ""}

        <!-- SECTION 2: Main Sensors -->
        ${this._selectedSection === "sensors" && this.config.entities?.length > 0 ? html`
          <div class="section">
            <div class="section-title">Main Sensors</div>
            <div class="entities">
              ${this.config.entities.map(item => {
                const stateObj = this.hass.states[item.entity_id];
                const state = stateObj?.state ?? "unavailable";
                const unit = item.unit || stateObj?.attributes?.unit_of_measurement || "";
                return html`
                  <div class="entity">
                    <span>${item.name || stateObj?.attributes?.friendly_name || item.entity_id}</span>
                    <span class="${state === "unavailable" ? "unavailable" : ""}">
                      ${state} ${unit}
                    </span>
                  </div>
                `;
              })}
            </div>
          </div>
        ` : ""}

        <!-- SECTION 3: Economic Rules -->
        ${this._selectedSection === "econ_rules" && this.config.econ_rules?.length > 0 ? html`
          <div class="section">
            <div class="section-title">Economic Rules</div>
            <select @change=${e => this._selectedRuleIndex = e.target.selectedIndex}>
              ${this.config.econ_rules.map((rule, i) => html`
                <option value="${i}" ?selected=${i === this._selectedRuleIndex}>
                  ${rule.name || `Rule ${i + 1}`}
                </option>
              `)}
            </select>

            ${ruleStateObj
              ? html`
                  <div class="rule">
                    <div class="rule-title">
                      ${selectedRule.name || `Rule ${this._selectedRuleIndex + 1}`}: ${ruleStateObj.state}
                    </div>
                    <div class="rule-grid">
                      <div>Enabled:</div><div>${ruleAttrs.enabled ?? "N/A"}</div>
                      <div>Start Time:</div><div>${ruleAttrs.start_time ?? "N/A"}</div>
                      <div>Stop Time:</div><div>${ruleAttrs.stop_time ?? "N/A"}</div>
                      <div>Start Date:</div><div>${ruleAttrs.start_date ?? "N/A"}</div>
                      <div>Stop Date:</div><div>${ruleAttrs.stop_date ?? "N/A"}</div>
                      <div>Days:</div><div>${ruleAttrs.days?.join(", ") ?? "N/A"}</div>
                      <div>Voltage:</div><div>${ruleAttrs.voltage_v !== undefined ? ruleAttrs.voltage_v + " V" : "N/A"}</div>
                      <div>SOC:</div><div>${ruleAttrs.soc_value !== undefined ? ruleAttrs.soc_value + " %" : "N/A"}</div>
                      <div>Power:</div><div>${ruleAttrs.power_w !== undefined ? ruleAttrs.power_w + " W" : "N/A"}</div>
                    </div>
                  </div>
                `
              : html`<div class="rule unavailable">Selected rule unavailable</div>`
            }
          </div>
        ` : ""}

        <!-- SECTION 4: Write Register -->
        ${this._selectedSection === "write_register" ? html`
          <div class="section">
            <div class="section-title">Write Register</div>
            <div class="write-section">
              <select @change=${e => this._selectedEntity = e.target.value}>
                <option value="">Select Inverter Entity</option>
                ${this._allEntities.map(eid => html`
                  <option value="${eid}">${eid}</option>
                `)}
              </select>

              <input
                type="text"
                placeholder="Register Key (e.g., econ_rule_1_enable)"
                @input=${e => this._selectedKey = e.target.value.trim()}
              />

              <input
                type="text"
                placeholder="Value (number or option text)"
                @input=${e => this._writeValue = e.target.value}
              />

              <button @click=${this._sendWrite}>Send</button>
              <div class="status">${this._selectedStatus || ""}</div>
            </div>
          </div>
        ` : ""}
      </ha-card>
    `;
  }
  _getEntityId(key) {
    // NEW: override support
    const override = this.config.overrides?.[key];
    if (override) return override;

    return `sensor.${this._entityPrefix}_${key}`;
  }
  _getValue(key) {
    const entity = this.hass.states[this._getEntityId(key)];
    return entity ? entity.state : null;
  }

  _getPower(key) {
    const val = this._getValue(key);
    return val != null ? Math.abs(val).toFixed(0) : "0";
  }

  _getBatteryIcon() {
    const soc = this._getValue("battery_capacity");
    if (soc == null) return "";
    const s = parseInt(soc);
    if (s >= 90) return "-100";
    if (s >= 80) return "-90";
    if (s >= 70) return "-80";
    if (s >= 60) return "-70";
    if (s >= 50) return "-60";
    if (s >= 40) return "-50";
    if (s >= 30) return "-40";
    if (s >= 20) return "-30";
    if (s >= 10) return "-20";
    return "-10";
  }

  _getBatteryState() {
    const power = this._getValue("battery_power");
    if (power == null) return "Idle";
    return power > 0 ? "Charging" : power < 0 ? "Discharging" : "Idle";
  }

  async _sendWrite() {
    if (!this._selectedEntity || !this._selectedKey || !this._writeValue) {
      this._selectedStatus = "Missing field";
      this.requestUpdate();
      return;
    }

    this._selectedStatus = "Sending...";
    this.requestUpdate();

    const prefixParts = this._selectedEntity.split(".");
    if (prefixParts.length < 3) return;
    const prefix = prefixParts[1];

    const isSelect = this._selectedKey.includes("enable") || this._selectedKey.includes("mode");
    const domain = isSelect ? "select" : "number";

    const fullKey = this._selectedKey.replace(/_/g, ".");
    const targetEntity = `${domain}.${prefix}_${fullKey}`;

    let service, data = {};

    if (domain === "number") {
      service = "number.set_value";
      data.value = parseFloat(this._writeValue) || 0;
    } else if (domain === "select") {
      service = "select.select_option";
      data.option = this._writeValue;
    } else {
      return;
    }

    try {
      await this.hass.callService(domain, service.split(".")[1], {
        entity_id: targetEntity,
        ...data,
      });

      this._selectedStatus = "Sent successfully!";
    } catch (err) {
      this._selectedStatus = "Send failed";
      console.error(err);
    }

    this._writeValue = "";
    this.requestUpdate();

    setTimeout(() => {
      this._selectedStatus = "";
      this.requestUpdate();
    }, 3000);
  }

  static get styles() {
    return css`
      ha-card {
        padding: 16px;
      }
      .header {
        font-size: 1.4em;
        font-weight: bold;
        margin-bottom: 16px;
        text-align: center;
      }
      .section-selector {
        margin-bottom: 16px;
      }
      .section-selector select {
        width: 100%;
        padding: 12px;
        font-size: 1em;
        font-weight: 500;
        border-radius: 8px;
        border: 2px solid var(--divider-color);
        background: var(--secondary-background-color);
        cursor: pointer;
        transition: all 0.2s;
      }
      .section-selector select:hover {
        border-color: var(--primary-color);
        background: var(--card-background-color);
      }
      .section {
        margin-bottom: 24px;
      }
      .section-title {
        font-size: 1.1em;
        font-weight: bold;
        margin-bottom: 8px;
        border-bottom: 1px solid var(--divider-color);
        padding-bottom: 4px;
      }
      .entities {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 8px;
      }
      .entity {
        display: flex;
        justify-content: space-between;
        background: var(--secondary-background-color);
        padding: 8px;
        border-radius: 4px;
      }
      .rule {
        background: var(--secondary-background-color);
        padding: 12px;
        border-radius: 8px;
        margin-top: 12px;
      }
      .rule-title {
        font-weight: bold;
        margin-bottom: 8px;
      }
      .rule-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 4px 12px;
        font-size: 0.9em;
      }
      .write-section {
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      select, input {
        padding: 8px;
        border-radius: 4px;
        border: 1px solid var(--divider-color);
        background: var(--card-background-color);
      }
      button {
        background: var(--primary-color);
        color: var(--text-primary-color);
        border: none;
        padding: 10px;
        border-radius: 4px;
        cursor: pointer;
        font-weight: bold;
      }
      button:hover {
        opacity: 0.9;
      }
      .unavailable {
        color: var(--error-color);
      }
      .status {
        text-align: center;
        font-weight: bold;
        color: var(--primary-color);
      }
    `;
  }
}

customElements.define("felicity-inverter-card", FelicityInverterCard);
