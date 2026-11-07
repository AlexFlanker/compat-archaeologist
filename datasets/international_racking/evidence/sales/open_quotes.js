// steel prices move too fast -- quotes are only valid for 30 days
const THIRTY_DAYS = 30 * 24 * 60 * 60 * 1000;
function isOpenQuote(order, today = new Date()) {
  return order.status === 0 && (today - order.order_date) <= THIRTY_DAYS;
}
