(() => {
  'use strict';

  function allocate1D(total, items, minProp, prefProp) {
    const n = items.length;
    if (n === 1) return [total];
    const mins = items.map(x => x[minProp]);
    const prefs = items.map(x => x[prefProp]);
    const sumMin = mins.reduce((a, b) => a + b, 0);
    const sumPref = prefs.reduce((a, b) => a + b, 0) || 1;
    const extra = Math.max(0, total - sumMin);

    const result = [...mins];
    if (extra <= 0) return result;

    const floats = prefs.map(p => extra * (p / sumPref));
    const floors = floats.map(Math.floor);
    let remainder = extra - floors.reduce((a, b) => a + b, 0);

    for (let i = 0; i < n; i++) {
      result[i] += floors[i];
    }

    const fractions = floats.map((f, i) => ({ i, frac: f - floors[i] }))
      .sort((a, b) => b.frac - a.frac || a.i - b.i);
    for (let k = 0; k < remainder; k++) {
      result[fractions[k].i]++;
    }
    return result;
  }

  function getValidConfigs(N) {
    const M = N - 1;
    const configs = [];
    // Bandless configurations (k_T = 0, k_B = 0)
    for (let kL = 1; kL <= M - 1; kL++) {
      const kR = M - kL;
      if (kR >= 1 && kL <= 3 && kR <= 3) {
        configs.push({ kT: 0, kB: 0, kL, kR, hasBands: false });
      }
    }
    // Band configurations (kT >= 1, kB >= 1, kL >= 1, kR >= 1)
    if (M >= 4) {
      for (let kT = 1; kT <= Math.min(4, M - 3); kT++) {
        for (let kB = 1; kB <= Math.min(4, M - 2 - kT); kB++) {
          for (let kL = 1; kL <= Math.min(3, M - 1 - kT - kB); kL++) {
            const kR = M - kT - kB - kL;
            if (kR >= 1 && kR <= 3 && Math.abs(kL - kR) <= 2 && Math.abs(kT - kB) <= 2) {
              configs.push({ kT, kB, kL, kR, hasBands: true });
            }
          }
        }
      }
    }
    return configs;
  }

  function assignBlocks(rest, { kT, kB, kL, kR }, random = false) {
    if (random) {
      const shuffled = [...rest];
      for(let i=shuffled.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[shuffled[i],shuffled[j]]=[shuffled[j],shuffled[i]];}
      return {
        leftList: shuffled.slice(0, kL),
        rightList: shuffled.slice(kL, kL + kR),
        topList: shuffled.slice(kL + kR, kL + kR + kT),
        bottomList: shuffled.slice(kL + kR + kT, kL + kR + kT + kB)
      };
    }

    // Rank blocks by vertical demand:
    // Tall, high-demand blocks prefer vertical middle regions (left/right columns).
    // Short, wide, or small blocks prefer horizontal bands (top/bottom rows).
    function getDemand(b) {
      const h = b.prefHeight || 160;
      const w = b.prefWidth || 280;
      const base = h * Math.sqrt(Math.max(1, h / w));
      // Mild affinity to keep request on left and result on right when sizes are comparable
      const middleAffinity = (b.id === 'request' || b.id === 'result') ? 20 : 0;
      return base + middleAffinity;
    }

    const sorted = [...rest].sort((a, b) => getDemand(b) - getDemand(a));
    const kMid = kL + kR;
    const middleBlocks = sorted.slice(0, kMid);
    const bandBlocks = sorted.slice(kMid);

    const leftList = [];
    const rightList = [];

    // Prioritize request -> left, result -> right if present in middleBlocks
    const remainingMiddle = [];
    for (const b of middleBlocks) {
      if (b.id === 'request' && leftList.length < kL) leftList.push(b);
      else if (b.id === 'result' && rightList.length < kR) rightList.push(b);
      else remainingMiddle.push(b);
    }

    // Distribute remaining middle blocks to balance total preferred height
    for (const b of remainingMiddle) {
      if (leftList.length >= kL) {
        rightList.push(b);
      } else if (rightList.length >= kR) {
        leftList.push(b);
      } else {
        const leftH = leftList.reduce((s, x) => s + x.prefH, 0);
        const rightH = rightList.reduce((s, x) => s + x.prefH, 0);
        if (leftH <= rightH) leftList.push(b);
        else rightList.push(b);
      }
    }

    // Distribute bandBlocks into topList (kT) and bottomList (kB)
    const topList = [];
    const bottomList = [];

    const topAffinities = new Set(['documents', 'memory', 'capabilities', 'request']);
    const remainingBand = [];

    for (const b of bandBlocks) {
      if (topAffinities.has(b.id) && topList.length < kT) topList.push(b);
      else if (!topAffinities.has(b.id) && bottomList.length < kB) bottomList.push(b);
      else remainingBand.push(b);
    }

    for (const b of remainingBand) {
      if (topList.length < kT) topList.push(b);
      else bottomList.push(b);
    }

    return { leftList, rightList, topList, bottomList };
  }

  function pack(blocks, { gap = 96, aspect = 1.4, random = false } = {}) {
    if (!Array.isArray(blocks) || blocks.length === 0) {
      throw new Error('At least one block is required.');
    }
    const input = blocks.map(b => {
      const minW = Math.max(120, Number.isFinite(b.minWidth) ? b.minWidth : Math.min(b.width || 280, 280));
      const minH = Math.max(80, Number.isFinite(b.minHeight) ? b.minHeight : Math.min(b.height || 160, 160));
      const prefW = Math.max(minW, Number.isFinite(b.width) ? b.width : minW);
      const prefH = Math.max(minH, Number.isFinite(b.height) ? b.height : minH);
      return {
        id: b.id,
        minWidth: minW,
        minHeight: minH,
        prefWidth: prefW,
        prefHeight: prefH,
        minW: minW + gap,
        minH: minH + gap,
        prefW: prefW + gap,
        prefH: prefH + gap
      };
    });

    const agent = input.find(b => b.id === 'agent');
    if (!agent) throw new Error('The agent block is required.');
    const rest = input.filter(b => b !== agent);

    if (rest.length === 0) {
      return {
        positions: { agent: [Math.round(24 + gap / 2), Math.round(24 + gap / 2)] },
        sizes: { agent: [agent.prefW - gap, agent.prefH - gap] },
        width: agent.prefW,
        height: agent.prefH,
        gap
      };
    }

    const configs = getValidConfigs(input.length);
    let best = null;

    for (const config of configs) {
      const { leftList, rightList, topList, bottomList } = assignBlocks(rest, config, random);

      const leftMinW = Math.max(0, ...leftList.map(b => b.minW));
      const rightMinW = Math.max(0, ...rightList.map(b => b.minW));
      const minWside = Math.max(leftMinW, rightMinW);

      const leftMinH = leftList.reduce((sum, b) => sum + b.minH, 0);
      const rightMinH = rightList.reduce((sum, b) => sum + b.minH, 0);
      const minHmid = Math.max(agent.minH, leftMinH, rightMinH);

      const leftPrefW = Math.max(0, ...leftList.map(b => b.prefW));
      const rightPrefW = Math.max(0, ...rightList.map(b => b.prefW));
      const prefWside = Math.max(minWside, leftPrefW, rightPrefW);

      const leftPrefH = leftList.reduce((sum, b) => sum + b.prefH, 0);
      const rightPrefH = rightList.reduce((sum, b) => sum + b.prefH, 0);
      const prefHmid = Math.max(minHmid, agent.prefH, leftPrefH, rightPrefH);

      let minHband = 0, prefHband = 0;
      let minWtop = 0, minWbottom = 0;
      let prefWtop = 0, prefWbottom = 0;

      if (config.hasBands) {
        minHband = Math.max(Math.max(...topList.map(b => b.minH)), Math.max(...bottomList.map(b => b.minH)));
        prefHband = Math.max(minHband, Math.max(...topList.map(b => b.prefH)), Math.max(...bottomList.map(b => b.prefH)));
        minWtop = topList.reduce((sum, b) => sum + b.minW, 0);
        minWbottom = bottomList.reduce((sum, b) => sum + b.minW, 0);
        prefWtop = topList.reduce((sum, b) => sum + b.prefW, 0);
        prefWbottom = bottomList.reduce((sum, b) => sum + b.prefW, 0);
      }

      const minWtotal = Math.max(2 * minWside + agent.minW, minWtop, minWbottom);
      const prefWtotal = Math.max(minWtotal, 2 * prefWside + agent.prefW, prefWtop, prefWbottom);

      const minHtotal = 2 * minHband + minHmid;
      const prefHtotal = 2 * prefHband + prefHmid;

      // Candidate dimensions adapting to content demand and target aspect
      const candidateSizes = [[prefWtotal, prefHtotal]];

      for (const hFrac of [0.65, 0.85, 1.0]) {
        const h = Math.max(minHtotal, Math.round(minHtotal + (prefHtotal - minHtotal) * hFrac));
        if (prefWtotal / h < aspect) {
          const targetW = Math.max(minWtotal, Math.round(h * aspect));
          for (const a of [0.0, 0.33, 0.67, 1.0]) {
            candidateSizes.push([Math.round(prefWtotal + (targetW - prefWtotal) * a), h]);
          }
        } else {
          const targetH = Math.max(minHtotal, Math.round(prefWtotal / aspect));
          for (const a of [0.0, 0.33, 0.67, 1.0]) {
            candidateSizes.push([prefWtotal, Math.round(prefHtotal + (targetH - prefHtotal) * a)]);
          }
        }
      }

      for (const [candW, candH] of candidateSizes) {
        // Apportion W into Wside and Wagent
        const deltaW = candW - (2 * minWside + agent.minW);
        const prefTotalW = 2 * prefWside + agent.prefW;
        const deltaWmid = deltaW * (agent.prefW / prefTotalW);
        const deltaWside = (deltaW - deltaWmid) / 2;
        let Wside = minWside + Math.round(deltaWside);
        let Wagent = candW - 2 * Wside;
        if (Wagent < agent.minW) {
          Wagent = agent.minW;
          Wside = Math.floor((candW - Wagent) / 2);
          Wagent = candW - 2 * Wside;
        }

        // Apportion H into Hband and Hmid
        let Hband = 0, Hmid = candH;
        if (config.hasBands) {
          const deltaH = candH - (2 * minHband + minHmid);
          const prefTotalH = 2 * prefHband + prefHmid;
          const deltaHmid = deltaH * (prefHmid / prefTotalH);
          const deltaHband = (deltaH - deltaHmid) / 2;
          Hband = minHband + Math.round(deltaHband);
          Hmid = candH - 2 * Hband;
          if (Hmid < minHmid) {
            Hmid = minHmid;
            Hband = Math.floor((candH - Hmid) / 2);
            Hmid = candH - 2 * Hband;
          }
        }

        const totalW = 2 * Wside + Wagent;
        const totalH = 2 * Hband + Hmid;

        // Build tiles
        const tiles = [{
          id: agent.id,
          x: Wside,
          y: Hband,
          w: Wagent,
          h: Hmid,
          prefW: agent.prefW,
          prefH: agent.prefH
        }];

        const leftHeights = allocate1D(Hmid, leftList, 'minH', 'prefH');
        let curY = Hband;
        for (let i = 0; i < leftList.length; i++) {
          const b = leftList[i];
          tiles.push({ id: b.id, x: 0, y: curY, w: Wside, h: leftHeights[i], prefW: b.prefW, prefH: b.prefH });
          curY += leftHeights[i];
        }

        const rightHeights = allocate1D(Hmid, rightList, 'minH', 'prefH');
        curY = Hband;
        for (let i = 0; i < rightList.length; i++) {
          const b = rightList[i];
          tiles.push({ id: b.id, x: Wside + Wagent, y: curY, w: Wside, h: rightHeights[i], prefW: b.prefW, prefH: b.prefH });
          curY += rightHeights[i];
        }

        if (config.hasBands && topList.length > 0) {
          const topWidths = allocate1D(totalW, topList, 'minW', 'prefW');
          let curX = 0;
          for (let i = 0; i < topList.length; i++) {
            const b = topList[i];
            tiles.push({ id: b.id, x: curX, y: 0, w: topWidths[i], h: Hband, prefW: b.prefW, prefH: b.prefH });
            curX += topWidths[i];
          }
        }

        if (config.hasBands && bottomList.length > 0) {
          const bottomWidths = allocate1D(totalW, bottomList, 'minW', 'prefW');
          let curX = 0;
          for (let i = 0; i < bottomList.length; i++) {
            const b = bottomList[i];
            tiles.push({ id: b.id, x: curX, y: Hband + Hmid, w: bottomWidths[i], h: Hband, prefW: b.prefW, prefH: b.prefH });
            curX += bottomWidths[i];
          }
        }

        // Multi-objective cost
        const curAspect = totalW / totalH;
        const aspectCost = Math.pow(Math.log(curAspect / aspect), 2) * 2.5;

        const prefArea = prefWtotal * prefHtotal;
        const actualArea = totalW * totalH;
        const excessArea = Math.max(0, actualArea / prefArea - 1);
        const compactCost = excessArea * 0.5;

        let distortionCost = 0;
        for (const t of tiles) {
          const aspDist = Math.pow(Math.log((t.w / t.h) / (t.prefW / t.prefH)), 2);
          const tActualArea = t.w * t.h;
          const tPrefArea = t.prefW * t.prefH;
          // Heavy penalty if content is starved of area
          const underArea = tActualArea < tPrefArea ? Math.pow(Math.log(tPrefArea / tActualArea), 2) * 2.5 : 0;
          const overArea = tActualArea > tPrefArea ? Math.pow(Math.log(tActualArea / tPrefArea), 2) * 0.4 : 0;
          distortionCost += aspDist * 1.2 + underArea + overArea;
        }
        distortionCost /= tiles.length;

        const towerPenalty = (!config.hasBands && input.length >= 5) ? 4.0 : 0;
        const balancePenalty = Math.abs(config.kL - config.kR) * 0.2 + (config.hasBands ? Math.abs(config.kT - config.kB) * 0.2 : 0);

        const totalCost = aspectCost + compactCost + distortionCost + towerPenalty + balancePenalty;

        if (!best || totalCost < best.cost) {
          best = { cost: totalCost, totalW, totalH, tiles };
        }
      }
    }

    if (!best) throw new Error('These block sizes cannot be packed.');

    const positions = {};
    const sizes = {};
    for (const t of best.tiles) {
      positions[t.id] = [Math.round(t.x + gap / 2 + 24), Math.round(t.y + gap / 2 + 24)];
      sizes[t.id] = [Math.round(t.w - gap), Math.round(t.h - gap)];
    }

    return {
      positions,
      sizes,
      width: best.totalW,
      height: best.totalH,
      gap
    };
  }

  window.ObserverBlockPacking = { pack };
})();
