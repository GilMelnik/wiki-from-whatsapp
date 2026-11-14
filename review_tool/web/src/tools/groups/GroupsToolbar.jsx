import { forwardRef } from "react";
import { Select } from "@shell";
import { entityOptions, ORDERS, SORTS, STANCE_FILTERS } from "./groups.js";

// Toolbar of a groups list: the mode tabs, search, filters and order. The withheld list
// needs no stance or flag filters (every group in it is a lone negative report).

function Flag({ label, checked, onChange }) {
  return (
    <label className="flex items-center gap-1 text-sm">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      {label}
    </label>
  );
}

export const GroupsToolbar = forwardRef(function GroupsToolbar({ modeTabs, query, onQuery, view, onView, summary, withheldList }, searchRef) {
  return (
    <>
      {modeTabs}
      <input
        ref={searchRef}
        type="search"
        dir="auto"
        className="field w-56"
        placeholder="Search text, entities… (f)"
        value={query}
        onChange={(event) => onQuery(event.target.value)}
      />
      {!withheldList && (
        <>
          <Flag label="Several claims" checked={view.multi} onChange={(multi) => onView({ multi })} />
          <Flag label="Disputed" checked={view.contradictions} onChange={(contradictions) => onView({ contradictions })} />
          <Flag label="Merge suggested" checked={view.suggested} onChange={(suggested) => onView({ suggested })} />
          <Flag label="Withheld" checked={view.withheld} onChange={(withheld) => onView({ withheld })} />
        </>
      )}
      <Select label="Entity" value={view.entity} options={entityOptions(summary?.entities)} onChange={(entity) => onView({ entity })} />
      {!withheldList && <Select label="Stance" value={view.stance} options={STANCE_FILTERS} onChange={(stance) => onView({ stance })} />}
      <Select label="Sort" value={view.sort} options={SORTS} onChange={(sort) => onView({ sort })} />
      <Select label="Order" value={view.order} options={ORDERS} onChange={(order) => onView({ order })} />
    </>
  );
});
