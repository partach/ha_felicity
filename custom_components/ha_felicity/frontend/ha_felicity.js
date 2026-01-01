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
    this._energyCache ??= {};
    this.showEnergyBar = true; // Set to false to hide the bar
  }

  static getConfigElement() {
    return document.createElement("felicity-inverter-card-editor");
  }

  _getOverride(key) {
    const o = this.config.overrides?.[key];
    if (!o) return null;
    if (typeof o === "string") {
      return { entity: o };
    }
    return o;
  }

  _convertEnergyToPower(key, entity) {
    const now = Date.now();
    const value = Number(entity.state);
    const prev = this._energyCache[key];
    this._energyCache[key] = { value, ts: now };
    if (!prev) return 0;
    const deltaKWh = value - prev.value;
    const deltaHours = (now - prev.ts) / 3_600_000;
    if (deltaHours <= 0) return 0;
    return Math.round((deltaKWh / deltaHours) * 1000);
  }

  setConfig(config) {
    this.config = {
      advanced: false,
      currency: '\u{20AC}',
      ...config,
    };
    this._selectedSection = "energy_flow";
  }

  getCardSize() {
    return 10;
  }

  updated(changedProps) {
    super.updated(changedProps);

    if (changedProps.has("hass")) {
      this._resolveDeviceEntities();
      this._drawEnergyBar();
      this._drawBatteryBar();
    }
  }

  _resolveDeviceEntities() {
    if (!this.config.device_id || !this.hass) return;

    const device = this.hass.devices?.[this.config.device_id];
    if (!device) return;

    const entityRegistry = this.hass.entities;
    if (!entityRegistry) return;

    this._deviceEntities = Object.values(entityRegistry)
      .filter(e => e.device_id === this.config.device_id)
      .map(e => e.entity_id);

    this._allEntities = this._deviceEntities.slice().sort();
  }

  _drawEnergyBar() {
    if (!this.showEnergyBar) return;

    const canvas = this.shadowRoot.querySelector('.bar-canvas');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    canvas.width = canvas.offsetWidth;
    canvas.height = canvas.offsetHeight;

    const width = canvas.width;
    const height = canvas.height;

    // Price data
    const currentPrice = parseFloat(this._getValue("current_price"));
    const minPrice = parseFloat(this._getValue("today_min_price"));
    const avgPrice = parseFloat(this._getValue("today_avg_price"));
    const maxPrice = parseFloat(this._getValue("today_max_price"));

    const level = parseFloat(this._getValue("price_threshold_level") || 5);

    const hasPriceData = !isNaN(currentPrice) && !isNaN(minPrice) && !isNaN(avgPrice) && !isNaN(maxPrice) && maxPrice > minPrice;

    // Calculate threshold price at current level
    
    let thresholdPrice = avgPrice; // fallback
    if (hasPriceData) {
      if (level <= 5) {
        const ratio = (level - 1) / 4.0;
        thresholdPrice = minPrice + (avgPrice - minPrice) * ratio;
      } else {
        const ratio = (level - 5) / 5.0;
        thresholdPrice = avgPrice + (maxPrice - avgPrice) * ratio;
      }
    }

    ctx.clearRect(0, 0, width, height);


    if (!hasPriceData) {
      // No data — show empty bar with "No data"
      ctx.font = '14px sans-serif';
      ctx.fillStyle = '#888';
      ctx.textAlign = 'center';
      ctx.fillText('No price data', width / 2, height / 2);
      return;
    }

    // Vertical bar background (min to max)
    const barWidth = width * 0.4;
    const barX = width / 2 - barWidth / 2;
    const barTop = 20;
    const barHeight = height - 40;

    ctx.fillStyle = '#33333388';
    ctx.fillRect(barX, barTop, barWidth, barHeight);

    // Function to map price to Y position (linear)
    const priceToY = (price) => {
      const ratio = (price - minPrice) / (maxPrice - minPrice);
      return barTop + barHeight - (ratio * barHeight); // invert Y
    };
    if (hasPriceData) {
      const thresholdY = priceToY(thresholdPrice);

      // Green part: from threshold down to bottom (cheap zone)
      ctx.fillStyle = '#4caf5088'; // semi-transparent green
      ctx.fillRect(barX, thresholdY, barWidth, barHeight + barTop - thresholdY);

      // Red part: from top down to threshold (expensive zone)
      ctx.fillStyle = '#f4433688'; // semi-transparent red
      ctx.fillRect(barX, barTop, barWidth, thresholdY - barTop);
    }


    // Draw 3 horizontal dotted lines
    const lines = [
      { price: avgPrice, color: '#4488ff', label: '', width: 1 },
      { price: thresholdPrice, color: '#137a08ff', label: '', width: 1 },
      { price: currentPrice, color: '#ffc800ff', label: '', width: 1 },
    ];

    ctx.setLineDash([6, 4]);

    lines.forEach(line => {
      const y = priceToY(line.price);

      ctx.beginPath();
      ctx.moveTo(barX - 5, y);
      ctx.lineTo(barX + barWidth + 10, y);

      ctx.strokeStyle = line.color;
      ctx.lineWidth = line.width;
      ctx.stroke();

      // Label on left
      ctx.font = '11px sans-serif';
      ctx.fillStyle = line.color;
      ctx.textAlign = 'right';
      ctx.fillText(`${line.label} ${line.price.toFixed(2)}`, barX - 5, y + 4);
    });

    ctx.setLineDash([]);

    // Min/Max labels
    ctx.font = '11px sans-serif';
    ctx.fillStyle = '#aaa';
    ctx.textAlign = 'center';
    ctx.fillText(`Max. Price: ${maxPrice.toFixed(2)}${this.config.currency}`, width / 2, barTop - 5);
    ctx.fillText(`Min. Price: ${minPrice.toFixed(2)}${this.config.currency}`, width / 2, barTop + barHeight + 15);
  }

  _drawBatteryBar() {
    if (!this.showEnergyBar) return;

    const canvas = this.shadowRoot.querySelector('.battery-bar-canvas');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    canvas.width = canvas.offsetWidth;
    canvas.height = canvas.offsetHeight;

    const width = canvas.width;
    const height = canvas.height;

    // Battery data
    const batteryVoltage = parseFloat(this._getValue("battery_voltage"));
    const batteryCapacity = parseFloat(this._getValue("battery_capacity")); // SOC %
    const dischargeDepth = parseFloat(this._getValue("discharge_depth_on_grid_bms")) || 20;
    const batteryCurrent = parseFloat(this._getValue("battery_current")) || 0;

    const hasBatteryData = !isNaN(batteryVoltage) && !isNaN(batteryCapacity);

    ctx.clearRect(0, 0, width, height);

    if (!hasBatteryData) {
      ctx.font = '14px sans-serif';
      ctx.fillStyle = '#888';
      ctx.textAlign = 'center';
      ctx.fillText('No battery data', width / 2, height / 2);
      return;
    }

    // Vertical bar setup
    const barWidth = width * 0.4;
    const barX = width / 2 - barWidth / 2;
    const barTop = 20;
    const barHeight = height - 40;

    // Function to map SOC % to Y position
    const socToY = (soc) => {
      return barTop + barHeight - ((soc / 100) * barHeight);
    };

    // Draw background (empty part - gray)
    ctx.fillStyle = '#33333388';
    ctx.fillRect(barX + 1, barTop -1, barWidth -1 , barHeight -1);

    // White outline around the battery bar
    ctx.strokeStyle = '#b9afafff';
    ctx.lineWidth = 2;
    ctx.strokeRect(barX, barTop, barWidth, barHeight);
    // Draw green part (charged portion)
    const currentY = socToY(batteryCapacity);
    ctx.fillStyle = '#4caf5088';
    ctx.fillRect(barX + 1 , currentY, barWidth - 1 , barHeight + barTop - currentY);

    // Draw yellow part (discharge depth protection zone)
    const dischargeY = socToY(dischargeDepth);
    ctx.fillStyle = '#ffc80088';
    ctx.fillRect(barX - 1 , dischargeY, barWidth - 1, barHeight + barTop - dischargeY);

    // Draw dotted line at discharge depth
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(barX - 5, dischargeY);
    ctx.lineTo(barX + barWidth + 10, dischargeY);
    ctx.strokeStyle = '#ff9800';
    ctx.lineWidth = 1;
    ctx.stroke();

    // Label for discharge depth
    ctx.font = '11px sans-serif';
    ctx.fillStyle = '#ff9800';
    ctx.textAlign = 'right';
    ctx.fillText(`${dischargeDepth.toFixed(0)}%`, barX - 5, dischargeY + 4);

    // Draw dotted line at current capacity
    ctx.beginPath();
    ctx.moveTo(barX - 5, currentY);
    ctx.lineTo(barX + barWidth + 10, currentY);
    ctx.strokeStyle = '#4caf50';
    ctx.lineWidth = 2;
    ctx.stroke();

    // Label for soc
    ctx.font = '11px sans-serif';
    ctx.fillStyle = '#48c021ff';
    ctx.textAlign = 'right';
    ctx.fillText(`${batteryCapacity.toFixed(0)}%`, barX - 5, currentY + 4);

    ctx.setLineDash([]);

    // Top label (Battery Voltage)
    ctx.font = '11px sans-serif';
    ctx.fillStyle = '#aaa';
    ctx.textAlign = 'center';
    ctx.fillText(`Voltage: ${batteryVoltage.toFixed(1)}V`, width / 2, barTop - 5);

    // Bottom label (Battery Current/Amperage)
    const currentLabel = batteryCurrent >= 0 
      ? `Amps: +${batteryCurrent.toFixed(1)}A` 
      : `Amps: ${batteryCurrent.toFixed(1)}A`;
    ctx.fillText(currentLabel, width / 2, barTop + barHeight + 15);
  }

  render() {
    if (!this.hass || !this.config) return html``;

    const selectedRule = this.config.econ_rules?.[this._selectedRuleIndex];
    const ruleStateObj = selectedRule ? this.hass.states[selectedRule.entity_id] : null;
    const ruleAttrs = ruleStateObj?.attributes || {};

    return html`
      <ha-card>
        ${this.config.name ? html`
          <div class="header">${this.config.name}</div>
        ` : ""}
        <!-- Section Selector -->
        ${this.config.advanced ? html`
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
        ` : ""}

        <!-- SECTION 1: Energy Flow -->
        ${this._selectedSection === "energy_flow" ? html`
          <div class="section energy-flow">
            <!-- Control Dropdowns -->
            <div class="flow-controls">
              ${this._renderGridModeSelect()} &nbsp
              ${this._renderPriceThresholdSelect()} 
            </div>           
            <div class="flow-diagram">
              <style>
                .card-root {
                  display: flex;
                  flex-direction: column;
                }              
                .flow-diagram {
                  position: relative;
                  height: 240px;
                  margin: 0px 0;
                  padding: 0px 0;
                }
                
                .flow-item {
                  position: absolute;
                  text-align: center;
                  display: flex;
                  flex-direction: column;
                  align-items: center;
                  gap: 2px;
                  z-index: 2;
                }

                .flow-item ha-icon {
                  margin-bottom: -2px;
                  --mdc-icon-size: 30px;
                  color: var(--secondary-text-color);
                }
                
                .power-value {
                  font-size: 1.2em;
                  font-weight: bold;
                  color: var(--primary-color, #03a9f4);
                }
                
                .soc {
                  color: var(--success-color, #4caf50);
                }
                .volt {
                  color: var(--success-color, #4caf50);
                }
                .label {
                  font-size: 0.85em;
                  color: var(--secondary-text-color);
                }
                .labelbold {
                  font-size: 1.1em;
                  font-weight: bold;
                  color: #f4b003ff;
                }
                .labelbold2 {
                  font-size: 1.1em;
                  font-weight: bold;
                  color: #3753cfff;
                }
                .pv { 
                  top: 10px; 
                  left: 15%; 
                  transform: translateX(-50%);
                  gap: 0px;
                }

                .grid { 
                  top: 10px; 
                  right: 15%; 
                  transform: translateX(50%);
                  gap: 0px;
                }
                
                .inverter { 
                  top: 48%; 
                  left: 50%; 
                  transform: translate(-50%, -50%);
                  flex-direction: column-reverse;
                  gap: 2px;
                }
                .state { 
                  top: 15%; 
                  left: 50%; 
                  transform: translate(-50%, -50%);
                  flex-direction: column-reverse;
                  gap: 2px;
                }
                
                .battery { 
                  bottom: 10px; 
                  left: 15%; 
                  transform: translateX(-50%);
                  flex-direction: row;
                  align-items: center;
                  gap: 2px;
                }
                
                .battery-info {
                  display: flex;
                  flex-direction: column;
                  gap: 2px;
                  text-align: left;
                }
                
                .home { 
                  bottom: 10px; 
                  right: 15%; 
                  transform: translateX(50%);
                  flex-direction: row;
                  align-items: center;
                  gap: 0px;
                }
                .home-info {
                  display: flex;
                  flex-direction: column;
                  gap: 2px;
                  text-align: left;
                }
                
                .backup { 
                  bottom: 10px; 
                  left: 50%; 
                  transform: translateX(-50%);
                  flex-direction: row;
                  align-items: center;
                  gap: 0px;                
                }
                
                .inverter ha-icon {
                  color: orange;
                  filter: drop-shadow(0 0 12px orange);
                }
                
                svg.flow-svg {
                  position: relative;
                  top: 0;
                  left: 0;
                  width: 100%;
                  height: 100%;
                  pointer-events: none;
                  z-index: 1;
                }

                .flow-controls {
                  display: flex;
                  flex-direction: row;
                  gap: 5px;
                  left: 10%;
                  align-items: center;
                  margin-bottom: 1px;
                  position: relative;
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
                .mirrored {
                  transform: scaleX(-1);
                }

                /* bar canvas */
                .bar-canvas-container {
                  position: absolute;
                  left: 65%;
                  top: 33%;
                  width: 39%;
                  height: 36%;
                  pointer-events: none;
                  z-index: 3;
                }
                .bar-canvas {
                  width: 100%;
                  height: 100%;
                }
                .battery-bar-canvas-container {
                  position: absolute;
                  left: -2%;
                  top: 33%;
                  width: 39%;
                  height: 36%;
                  pointer-events: none;
                  z-index: 3;
                }
                .battery-bar-canvas {
                  width: 100%;
                  height: 100%;
                }
              </style>

              <svg class="flow-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
                <path 
                  class="flow-path charging ${this._getRawPower('pv_input_power') > 50 ? 'active' : 'inactive'}" 
                  d="M 25 25 L 47 48" 
                  vector-effect="non-scaling-stroke"
                />
                <!-- Grid to Inverter (bidirectional) -->
                <path 
                  class="flow-path ${(() => {
                    const power = parseFloat(this._getValue('total_ac_input_power')) || 0;
                    if (Math.abs(power) <= 50) return 'inactive';
                    if (power > 0) return 'active grid-import';
                    return 'active grid-export reverse';
                  })()} " 
                  d="M 75 25 L 53 48" 
                  vector-effect="non-scaling-stroke"
                />
                <!-- Inverter to Battery (bidirectional) -->
                <path 
                  class="flow-path ${(() => {
                    const power = parseFloat(this._getRawPower('battery_power')) || 0;
                    const state = this._getBatteryState();
                    if (power <= 50) return 'inactive';
                    if (state === 'Charging') return 'active charging';
                    if (state === 'Discharging') return 'active discharging reverse';
                    return 'inactive';
                  })()} " 
                  d="M 47 52 L 25 75" 
                  vector-effect="non-scaling-stroke"
                />
                <!-- Home load -->
                <path 
                  class="flow-path ${this._getRawPower('loadpower_lineside') > 50 ? 'active' : 'inactive'}" 
                  d="M 53 52 L 75 75" 
                  vector-effect="non-scaling-stroke"
                />
                <!-- Backup load -->
                <path 
                  class="flow-path ${this._getRawPower('total_ac_output_active_power') > 50 ? 'active' : 'inactive'}" 
                  d="M 50 54 L 50 74" 
                  vector-effect="non-scaling-stroke"
                />
                <path 
                  class="flow-path 'inactive'}" 
                  d="M 47 42 L 53 42" 
                  vector-effect="non-scaling-stroke"
                />
                <path 
                  class="flow-path 'inactive'}" 
                  d="M 53 42 L 53 55" 
                  vector-effect="non-scaling-stroke"
                />
                <path 
                  class="flow-path 'inactive'}" 
                  d="M 53 55 L 47 55" 
                  vector-effect="non-scaling-stroke"
                />
                <path 
                  class="flow-path 'inactive'}" 
                  d="M 47 55 L 47 42" 
                  vector-effect="non-scaling-stroke"
                />
              </svg>

              <div class="flow-item pv">
                <ha-icon 
                  .hass=${this.hass} icon="${(() => {
                    const power = parseFloat(this._getValue('pv_input_power')) || 0;
                    if (power > 0) return 'mdi:solar-power-variant';
                    if (power <= 0) return 'mdi:solar-panel';
                    return 'mdi:solar-panel';
                  })()}"
                ></ha-icon>
                <div class="power-value">${this._getPower("pv_input_power")}</div>
                <div class="label"></div>
              </div>

              <div class="flow-item grid">
                <ha-icon 
                  .hass=${this.hass} 
                  class="grid-icon ${(() => {
                    const power = parseFloat(this._getValue('total_ac_input_power')) || 0;
                    return power > 50 ? 'mirrored' : '';  // mirror only on export
                  })()}"
                  icon="${(() => {
                    const power = parseFloat(this._getValue('total_ac_input_power')) || 0;
                    if (power > 50) return 'mdi:transmission-tower-export';
                    if (power < -50) return 'mdi:transmission-tower-import';
                    return 'mdi:transmission-tower';
                  })()}"
                ></ha-icon>
                <div class="power-value">${this._getPower("total_ac_input_power")}</div>
                <div class="label"></div>
              </div>
              <div class="flow-item state">
                <div class="label">
                  Operation: ${this._getStateLabel("operating_mode")}
                </div>  
                <div class="label">
                  Price now: <span class="labelbold">${this._getStateLabel("current_price")}${this.config.currency}</span>
                  --> <span class="labelbold2">${this._getStateLabel("energy_state")}</span>
                </div>
              </div>
              
              <div class="flow-item inverter">
                <ha-icon .hass=${this.hass} icon="mdi:lightning-bolt"></ha-icon>
              </div>

              <div class="flow-item battery">
                <ha-icon .hass=${this.hass} icon="${this._getBatteryIcon()}"></ha-icon>
                <div class="soc">
                  <div class="power-value">${this._getPower("battery_power")}</div>
                  <div class="label">${this._getBatteryState()}</div>
                </div>
              </div>

              <div class="flow-item home">
                <ha-icon .hass=${this.hass} icon="mdi:home-lightning-bolt"></ha-icon>
                <div class="battery-info">
                  <div class="power-value">${this._getPower("loadpower_lineside")}</div>
                  <div class="label">Home Load</div>
                </div>  
              </div>

              <div class="flow-item backup">
                <ha-icon .hass=${this.hass} icon="mdi:home-battery-outline"></ha-icon>
                <div class="battery-info">
                  <div class="power-value">${this._getPower("total_ac_output_active_power") || 0}</div>
                  <div class="label">Backup Load</div>
                </div>
              </div>

              <!-- Bar Canvas -->
              ${this.showEnergyBar ? html`
                <div class="bar-canvas-container">
                  <canvas class="bar-canvas"></canvas>
                </div>
                <div class="battery-bar-canvas-container">
                  <canvas class="battery-bar-canvas"></canvas>
                </div>
              ` : ""}
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
    const override = this._getOverride(key);
    if (override?.entity) return override.entity;

    if (!this._deviceEntities?.length) return null;

    return this._deviceEntities.find(eid =>
      eid.endsWith(`_${key}`)
    );
  }

  _getValue(key) {
    const override = this._getOverride(key);
    const entityId = override?.entity ?? this._getEntityId(key);
    const entity = this.hass.states[entityId];
    if (!entity) return null;

    const unit = entity.attributes?.unit_of_measurement;

    if (
      override?.mode === "energy_to_power" &&
      unit?.toLowerCase().includes("wh")
    ) {
      return this._convertEnergyToPower(key, entity);
    }

    return Number(entity.state);
  }

  _truncateFromSecondSpace(text) {
    if (!text) return text;
    const parts = String(text).split(" ");
    return parts.length > 2 ? parts.slice(0, 2).join(" ") : text;
  }

  _getStateLabel(key) {
    const state = this._getState(key);
    const label = state === "—" ? "Unknown" : state;
    return this._truncateFromSecondSpace(label);
  }

  _getState(key) {
    const entityId = this._getEntityId(key);
    if (!entityId) return "—";

    const entity = this.hass.states[entityId];
    if (!entity) return "—";

    const domain = entityId.split(".")[0];

    // Select → textual state
    if (domain === "select") {
      return entity.state;
    }

    // Number / Sensor → numeric if possible
    if (domain === "number" || domain === "sensor") {
      const val = Number(entity.state);
      return isNaN(val) ? entity.state : val;
    }

    // Fallback (switch, binary_sensor, etc.)
    return entity.state ?? "—";
  }

  _getRawPower(key) {
    const val = this._getValue(key);
    return val != null ? Math.abs(val).toFixed(0) : "0";
  }

  _getPower(key) {
    const val = this._getValue(key);
    if (val == null) return "0 W";

    const absVal = Math.abs(val);
    if (absVal >= 1000) {
      return (absVal / 1000).toFixed(2) + " kW";
    }
    return absVal.toFixed(0) + " W";
  }

  _getBatteryIcon() {
    const soc = parseFloat(this._getState("battery_capacity"));
    if (soc == null || isNaN(soc)) return "mdi:battery-charging";

    const val = Math.max(0, Math.min(100, Math.round(soc)));

    // 6. Logic: Check from highest to lowest
    if (val >= 95) return "mdi:battery";
    if (val >= 85) return "mdi:battery-90";
    if (val >= 75) return "mdi:battery-80";
    if (val >= 65) return "mdi:battery-70";
    if (val >= 55) return "mdi:battery-60";
    if (val >= 45) return "mdi:battery-50";
    if (val >= 35) return "mdi:battery-40";
    if (val >= 25) return "mdi:battery-30";
    if (val >= 15) return "mdi:battery-20";
    if (val >= 5)  return "mdi:battery-10";
    
    return "mdi:battery-outline";
  }

  _getBatteryState() {
    const power = this._getValue("battery_power");
    if (power == null) return "Idle";
    return power > 0 ? "Charging" : power < 0 ? "Discharging" : "Idle";
  }

  _renderGridModeSelect() {
    const entityId = this._getEntityId("grid_mode");
    if (!entityId) return html``;

    const entity = this.hass.states[entityId];
    if (!entity) return html``;

    const currentValue = entity.state;
    const options = entity.attributes?.options || [];

    return html`
      <div class="control-group">
        <span class="label">Grid Mode:</span>
        <select 
          @change=${(e) => this._handleGridModeChange(entityId, e.target.value)}
          .value=${currentValue}
        >
          ${options.map(opt => html`
            <option value="${opt}" ?selected=${opt === currentValue}>
              ${opt}
            </option>
          `)}
        </select>
      </div>
    `;
  }

  
  _renderPriceThresholdSelect() {
    const minPrice = this._getValue("today_min_price");
    const avgPrice = this._getValue("today_avg_price");
    const maxPrice = this._getValue("today_max_price");
    const thresholdEntityId = this._getEntityId("price_threshold_level");

    if (!thresholdEntityId || minPrice == null || avgPrice == null || maxPrice == null) {
      return html``;
    }

    const thresholdEntity = this.hass.states[thresholdEntityId];
    if (!thresholdEntity) return html``;

    const currentLevel = parseInt(thresholdEntity.state) || 1;
    const priceOptions = this._calculatePriceThresholds(minPrice, avgPrice, maxPrice);

    return html`
      <div class="control-group">
        <span class="label">Price Threshold:</span>
        <select 
          @change=${(e) => this._handlePriceThresholdChange(thresholdEntityId, e.target.value)}
          .value=${currentLevel}
        >
          ${priceOptions.map((opt, index) => {
            const level = index + 1;
            return html`
              <option value="${level}" ?selected=${level === currentLevel}>
                ${level}: ${opt.toFixed(3)} ${this.config?.currency ?? '€'}
              </option>
            `;
          })}
        </select>
      </div>
    `;
  }

  _calculatePriceThresholds(minPrice, avgPrice, maxPrice) {
    const thresholds = [];
    
    for (let level = 1; level <= 10; level++) {
      let threshold;
      if (level <= 5) {
        const ratio = (level - 1) / 4.0;
        threshold = minPrice + (avgPrice - minPrice) * ratio;
      } else {
        const ratio = (level - 5) / 5.0;
        threshold = avgPrice + (maxPrice - avgPrice) * ratio;
      }
      thresholds.push(threshold);
    }
    
    return thresholds;
  }

  async _handleGridModeChange(entityId, value) {
    try {
      await this.hass.callService("select", "select_option", {
        entity_id: entityId,
        option: value,
      });
    } catch (err) {
      console.error("Failed to change grid mode:", err);
    }
  }

  async _handlePriceThresholdChange(entityId, level) {
    try {
      await this.hass.callService("number", "set_value", {
        entity_id: entityId,
        value: parseInt(level),
      });
    } catch (err) {
      console.error("Failed to change price threshold level:", err);
    }
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
        padding: 2px;
      }
      .header {
        font-size: 1.4em;
        font-weight: bold;
        margin-bottom: 3px;
        text-align: center;
      }
      .section-selector {
        margin-bottom: 3px;
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
      .section.energy-flow {
        margin-top: 1px;
        margin-bottom: 1px;
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

class FelicityInverterCardEditor extends LitElement {
  static get properties() {
    return {
      hass: {},
      _config: {},
    };
  }

  setConfig(config) {
    this._config = { ...config };
  }

  get _deviceId() {
    return this._config.device_id || "";
  }

  render() {
    if (!this.hass) return html``;

    return html`
      <ha-form
        .hass=${this.hass}
        .data=${this._config}
        .schema=${this._schema()}
        @value-changed=${this._valueChanged}
      ></ha-form>
    `;
  }

  _schema() {
    return [
      {
        name: "name",
        selector: { text: {} },
      },
      {
        name: "device_id",
        selector: {
          device: {
            integration: "ha_felicity",
          },
        },
      },
      {
        name: "overrides",
        selector: {
          object: {},
        },
      },
    ];
  }

  _valueChanged(ev) {
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: ev.detail.value },
        bubbles: true,
        composed: true,
      })
    );
  }
}


customElements.define("felicity-inverter-card", FelicityInverterCard);
customElements.define("felicity-inverter-card-editor",FelicityInverterCardEditor);

(function () {
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "felicity-inverter-card",
    name: "Felicity Inverter Card",
    description: "Visualize Felicity Inverter", 
    preview: true,
  });
})();
