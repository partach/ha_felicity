    if (!this.showSineWave) return;

    const canvas = this.shadowRoot.querySelector('.sine-canvas');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    canvas.width = canvas.offsetWidth;
    canvas.height = canvas.offsetHeight;

    const width = canvas.width;
    const height = canvas.height;

    const level = parseFloat(this._getValue("price_threshold_level") || 5); // 1-10
    const gridMode = this._getValue("grid_mode") || "off";

    // Price data
    const currentPrice = parseFloat(this._getValue("current_price"));
    const minPrice = parseFloat(this._getValue("today_min_price"));
    const avgPrice = parseFloat(this._getValue("today_avg_price"));
    const maxPrice = parseFloat(this._getValue("today_max_price"));

    const hasPriceData = !isNaN(currentPrice) && !isNaN(minPrice) && !isNaN(avgPrice) && !isNaN(maxPrice) && maxPrice > minPrice;

    // Full amplitude: use almost full canvas height
    const maxAmplitudePixels = (height - 10) / 2;

    // Linear fraction below X-axis = level / 10
    const fractionBelow = 1 - (level / 10);

    // Offset in pixels: positive = move up, negative = move down
    const offsetPixels = (0.5 - fractionBelow) * 2 * maxAmplitudePixels;

    const positiveColor = gridMode === 'to_grid' ? '#4caf50' : '#f44336';
    const negativeColor = gridMode === 'to_grid' ? '#f44336' : '#4caf50';

    ctx.clearRect(0, 0, width, height);

    // X-axis in middle (dotted)
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(0, height / 2);
    ctx.lineTo(width, height / 2);
    ctx.strokeStyle = '#2a67c9ff';
    ctx.lineWidth = 0.5;
    ctx.stroke();

    // Draw sine wave (threshold curve)
    const points = 100;
    ctx.lineWidth = 1;

    for (let i = 0; i <= points; i++) {
      const x = (i / points) * width;
      const phase = (i / points) * Math.PI * 2;
      let y = height / 2 + Math.sin(phase) * maxAmplitudePixels + offsetPixels;

      y = Math.max(5, Math.min(height - 5, y));

      if (i === 0) {
        ctx.beginPath();
        ctx.moveTo(x, y);
      } else {
        ctx.lineTo(x, y);
      }

      const isPositive = y < height / 2;
      ctx.strokeStyle = isPositive ? positiveColor : negativeColor;
      ctx.stroke();

      if (i < points) {
        ctx.beginPath();
        ctx.moveTo(x, y);
      }
    }

    // === Yellow line: current price relative to threshold ===
    if (hasPriceData) {
      // Calculate threshold price at current level (same as coordinator)
      let thresholdPrice;
      if (level <= 5) {
        const ratio = (level - 1) / 4.0;
        thresholdPrice = minPrice + (avgPrice - minPrice) * ratio;
      } else {
        const ratio = (level - 5) / 5.0;
        thresholdPrice = avgPrice + (maxPrice - avgPrice) * ratio;
      }

      // Difference from threshold (positive = current > threshold)
      const priceDiff = currentPrice - thresholdPrice;

      // Scale difference using same amplitude as wave
      const diffPixels = priceDiff * (maxAmplitudePixels / 10); // 10 = full amplitude

      // Y position: center minus offset (higher price = higher on canvas)
      let priceY = height / 2 + offsetPixels - diffPixels;

      // Clamp
      priceY = Math.max(5, Math.min(height - 5, priceY));

      // Draw dotted glowing yellow line
      ctx.setLineDash([6, 4]);
      ctx.beginPath();
      ctx.moveTo(45, priceY);
      ctx.lineTo(width - 45, priceY);

      ctx.shadowBlur = 12;
      ctx.shadowColor = "#e7d690ff";
      ctx.strokeStyle = "#cf730aff";
      ctx.lineWidth = 2;
      ctx.stroke();

      ctx.shadowBlur = 0;
      ctx.setLineDash([]);
    }
  }
