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
      this._updateEntitiesAndKeys();
    }
  }

  _updateEntitiesAndKeys() {
    // All Felicity entities (for inverter picker)
    this._allEntities = Object.keys(this.hass.states)
      .filter(eid => eid.includes("felicity_inverter"))
      .sort();

    // Writable registers – clean key (remove "felicity_inverter_")
    this._keyOptions = Object.keys(this.hass.states)
      .filter(eid => 
        eid.includes("felicity_inverter") &&
        (eid.startsWith("number.") || eid.startsWith("select."))
      )
      .map(eid => {
        // number.felicity_inverter_econ_rule_1_enable → econ_rule_1_enable
        const parts = eid.split(".");
        return parts.slice(2).join("_");
      })
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

        ${this.config.entities?.length > 0
          ? html`
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
            `
          : ""}

        ${this.config.econ_rules?.length > 0
          ? html`
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
            `
          : ""}

          <div class="section">
            <div class="section-title">Write Register</div>
            <div class="write-section">
                <!-- Dropdown 1: Select inverter (any entity) -->
                <select @change=${e => this._selectedEntity = e.target.value}>
                <option value="">Select Inverter Entity</option>
                ${this._allEntities.map(eid => html`
                    <option value="${eid}">${eid}</option>
                `)}
                </select>

                <!-- Text input for key (manual) -->
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

                <button @click=${this._sendWrite}>
                Send
                </button>
                <div class="status">${this._selectedStatus || ""}</div>
            </div>
          </div>
      </ha-card>
    `;
  }

  async _sendWrite() {
    if (!this._selectedEntity || !this._selectedKey || !this._writeValue) {
        this._selectedStatus = "Missing field";
        this.requestUpdate();
        return;
    }

    this._selectedStatus = "Sending...";
    this.requestUpdate();

    // Get prefix from selected entity (e.g., sensor.living_room_inverter_battery_voltage → living_room_inverter)
    const prefixParts = this._selectedEntity.split(".");
    if (prefixParts.length < 3) return;
    const prefix = prefixParts[1];  // the device name

    // Guess domain
    const isSelect = this._selectedKey.includes("enable") || this._selectedKey.includes("mode");
    const domain = isSelect ? "select" : "number";

    // Reconstruct with actual prefix
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

    // Clear status after 3 seconds
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
    `;
  }
}

customElements.define("felicity-inverter-card", FelicityInverterCard);
