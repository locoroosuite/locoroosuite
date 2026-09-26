/*
 * LRCal overlap layout engine (U12.56a).
 * Assigns side-by-side columns to concurrent events so they never stack.
 * Classic cluster/greedy-column algorithm used by calendar apps.
 */
(function () {
  'use strict';

  var LRCal = (window.LRCal = window.LRCal || {});

  /*
   * items: [{ startMin, endMin, ... }] (minutes since midnight of the day).
   * Returns the same items with `col` (0-based) and `cols` (cluster width)
   * set so overlapping events share the lane proportionally.
   */
  function layoutOverlaps(items) {
    var sorted = items.slice().sort(function (a, b) {
      return a.startMin - b.startMin || b.endMin - a.endMin;
    });
    var cluster = [];
    var clusterEnd = -1;

    function flushCluster() {
      if (!cluster.length) return;
      var columns = []; // per-column last endMin
      cluster.forEach(function (item) {
        var placed = false;
        for (var c = 0; c < columns.length; c++) {
          if (item.startMin >= columns[c]) {
            columns[c] = item.endMin;
            item.col = c;
            placed = true;
            break;
          }
        }
        if (!placed) {
          columns.push(item.endMin);
          item.col = columns.length - 1;
        }
      });
      cluster.forEach(function (item) {
        item.cols = columns.length;
      });
      cluster = [];
    }

    sorted.forEach(function (item) {
      item.col = 0;
      item.cols = 1;
      if (cluster.length && item.startMin >= clusterEnd) {
        flushCluster();
        clusterEnd = -1;
      }
      cluster.push(item);
      clusterEnd = Math.max(clusterEnd, item.endMin);
    });
    flushCluster();
    return sorted;
  }

  LRCal.layoutOverlaps = layoutOverlaps;
})();
