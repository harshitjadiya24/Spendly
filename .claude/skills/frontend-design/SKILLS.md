# Expense Tracker — Profile Page

A dashboard + full transaction ledger for the profile page, built around a "checkbook ledger" concept instead of a generic finance-app template.

## Design concept

- **Palette:** cool slate-paper background, deep forest-charcoal ink, teal primary accent, muted category colors (no neons).
- **Typography:** Fraunces (serif) for the balance headline, Inter for UI text, IBM Plex Mono for all numbers, dates, and amounts.
- **Signature element:** a hand-built SVG arc gauge for category spending (not a stock donut chart), plus a running-balance column in the transaction table that recalculates chronologically like a real checkbook register.

## Features

- **Balance hero** — current balance, income this month, spending this month, all recalculated live.
- **Category dial** — proportional arc segments per spending category with a legend.
- **Transaction ledger** — searchable by merchant, filterable by category, shows a true running balance per row.
- **Add transaction** — inline form for income or expense entries that updates every figure on the page instantly.

## Files

- `expense-tracker-profile.jsx` — the React component (Tailwind core utilities + custom CSS variables, `lucide-react` icons).

## Data shape

```js
{
  id: "t1",
  date: "2026-06-02",       // ISO yyyy-mm-dd
  merchant: "Meridian Market",
  category: "Groceries",     // one of the CATS keys, or "Income"
  type: "expense",           // "expense" | "income"
  amount: 86.42
}
```

## Customizing

- **Colors/fonts:** edit the CSS variables and `@import` in the `<style>` block at the top of the component.
- **Categories:** edit the `CATS` object — add a category with a hex color and it flows through the dial, legend, and ledger tags automatically.
- **Opening balance:** set via `OPENING_BALANCE`.
- **Real data:** replace `SEED` with your fetched transactions in the same shape, or pass them in as a prop.

## Next steps to consider

- Monthly view toggle / date-range filter
- Sorting the ledger by amount or category
- Pagination or virtualization for large transaction histories