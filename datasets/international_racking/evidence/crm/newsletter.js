// active contacts for the quarterly product newsletter
const recipients = await db.collection("contacts")
  .find({ is_deleted: { $ne: true } })
  .project({ email: 1, name: 1 })
  .toArray();
