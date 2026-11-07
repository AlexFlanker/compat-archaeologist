<?php
// order status codes -- DO NOT CHANGE, the production planning app reads these
function status_label($code) {
  switch ($code) {
    case 0: return "Quote";
    case 1: return "Confirmed";
    case 2: return "In production";
    case 3: return "Shipped";
    case 4: return "Cancelled";
    default: return "Unknown";
  }
}
