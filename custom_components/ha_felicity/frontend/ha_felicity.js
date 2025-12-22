import { LitElement, html, css } from "https://unpkg.com/lit?module";

class FelicityInverterCard extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      config: { type: Object },
      _selectedRuleIndex: { type: Number },
      _selectedEntity: { type: String },
      _selectedKey: { type: String }, // Fixed typo: was _selectedKeyEntity in constructor
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
    this._selectedKey = "";
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
    // Filter to find entities belonging to this integration
    // Note: This relies on the user naming them "felicity..." or similar.
    // A more robust way is to just list all and let user pick, or config the prefix.
    this._allEntities = Object.keys(this.hass.states)
      .filter(eid => eid.includes("felicity") || eid.includes("inverter"))
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
            <div class="section-title">Write Register (Advanced)</div>
            <div class="write-section">
                <!-- Dropdown 1: Select target entity -->
                <select @change=${e => this._selectedEntity = e.target.value}>
                <option value="">Select Target Entity</option>
                ${this._allEntities.map(eid => html`
                    <option value="${eid}">${eid}</option>
                `)}
                </select>

                <!-- Text input for key -->
                <input
                type="text"
                placeholder="Register Key (e.g., charge_current_limit)"
                @input=${e => this._selectedKey = e.target.value.trim()}
                />

                <!-- Text input for value -->
                <input
                type="text"
                placeholder="Value (e.g., 50)"
                @input=${e => this._writeValue = e.target.value}
                />

                <button @click=${this._sendWrite}>
                Send Write Command
                </button>
                <div class="status">${this._selectedStatus || ""}</div>
            </div>
          </div>
      </ha-card>
    `;
  }

  async _sendWrite() {
    if (!this._selectedEntity || !this._selectedKey || !this._writeValue) {
        this._selectedStatus = "Missing field (Entity, Key, or Value)";
        this.requestUpdate();
        return;
    }

    this._selectedStatus = "Sending...";
    this.requestUpdate();

    // Try to convert to number if it looks like one, otherwise keep as string
    let finalValue = this._writeValue;
    if (!isNaN(parseFloat(finalValue)) && isFinite(finalValue)) {
        finalValue = parseFloat(finalValue);
    }

    try {
        console.log("Calling write_register service with:", {
            entity_id: this._selectedEntity,
            key: this._selectedKey,
            value: finalValue
        });

        // Use the custom service ha_felicity.write_register defined in services.yaml
        // We pass the entity_id so the integration knows WHICH inverter to write to.
        await this.hass.callService("ha_felicity", "write_register", {
            entity_id: this._selectedEntity,
            key: this._selectedKey,
            value: finalValue
        });

        this._selectedStatus = "Sent successfully!";
    } catch (err) {
        this._selectedStatus = `Failed: ${err.message}`;
        console.error(err);
    }

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
        color: var(--primary-text-color);
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
        font-style: italic;
        text-align: center;
        min-height: 1.2em;
      }
    `;
  }
}

customElements.define("felicity-inverter-card", FelicityInverterCard);
