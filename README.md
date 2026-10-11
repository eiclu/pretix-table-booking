# pretix-tables: rooms with numbered tables

A pretix plugin for selling seats at tables. A room is a product, its price is the price per
seat, and each room has a list of tables with numbered seats. Buyers open a room, pick a table
and choose their seats in a popup; no seat can be reserved or sold twice.

## How it works

- On a product's page in the backend, enable **Room with numbered tables**, set the number of
  tables and seats per table, optionally **Sell in pairs only**, and upload a room map image.
  The product must not have variations.
- **Products → Rooms and tables** lists all rooms. "Manage tables" lets you add tables in bulk,
  rename them, change their size and delete tables without reservations.
- Per table you can mark seats as **not for sale** (for example "3-4, 9") when they do not exist
  at that table or are given out outside the system. They keep their number, show as not
  available in the shop and do not count towards the quota.
- Each seat is a pretix `Seat`. pretix itself therefore guarantees that a seat is held by at
  most one cart or order, shows it in the cart, the order and on tickets ("Table 7, Seat 5"),
  and frees it when a cart expires or an order is cancelled.
- The plugin creates and maintains one quota per room, sized to the sum of its tables' seats.
  Additional quotas you add yourself still apply.
- Where a room is offered follows its product category: a normal category puts it on the shop
  front page (in a block above the regular product list), a cross-selling category puts it in
  the "Reserve seats" checkout step, "normal + cross-selling" does both.
- The popup only fills in the form; seats are reserved when the buyer presses "Add to cart"
  or "Continue".
- With pairs enabled, seats 1+2, 3+4, … of a table can only be bought together. Removing one
  of them from the cart removes the other; checkout is refused for incomplete pairs.
- **Orders → Export → Table reservations** gives one row per reserved seat (room, table, seat,
  order, names, e-mail) as CSV or Excel.

## Limitations

- Event series (subevents) are not supported.
- Can not be combined with a seating plan on the same event.
- Rooms are not shown in the pretix widget, on voucher redemption pages or inside pretix' own
  cross-selling step (they get their own checkout step instead).

## Development

    pip install -e .
    python -m pytest tests
