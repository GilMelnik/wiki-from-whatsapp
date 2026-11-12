import { forwardRef } from "react";
import { Kbd, Select } from "@shell";
import { FLAG_FILTERS, ORDERS, SORTS, STATUS_FILTERS } from "./entities.js";

// Toolbar: search, which entities are listed and in what order (needs review first by
// default), and the names of deleted entities (only when there are some).

export const Toolbar = forwardRef(function Toolbar({ query, onQuery, view, onView, deletedNames, onDeletedNames }, searchRef) {
  return (
    <>
      <span className="flex items-center gap-1">
        <input
          ref={searchRef}
          type="search"
          dir="auto"
          className="field w-56"
          placeholder="Search names or an ID"
          value={query}
          onChange={(event) => onQuery(event.target.value)}
        />
        <Kbd>f</Kbd>
      </span>
      <Select label="Status" value={view.status} options={STATUS_FILTERS} onChange={(status) => onView({ status })} />
      <Select label="Flagged" value={view.flag} options={FLAG_FILTERS} onChange={(flag) => onView({ flag })} />
      <Select label="Order" value={view.sort} options={SORTS} onChange={(sort) => onView({ sort })} />
      {view.sort !== "review" && (
        <Select label="Direction" value={view.order} options={ORDERS} onChange={(order) => onView({ order })} />
      )}
      {deletedNames > 0 && (
        <button type="button" className="btn btn-sm btn-quiet ms-auto" onClick={onDeletedNames}>
          Names of deleted entities…
        </button>
      )}
    </>
  );
});
