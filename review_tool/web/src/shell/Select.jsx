// A labelled drop-down for a tool's toolbar (filter, sort, order).
//
//   <Select label="Sort" value={sort} onChange={setSort}
//           options={[{ value: "count", label: "Mentions" }, { value: "id", label: "Id" }]} />
//
// `onChange` receives the chosen option's value (a string).

export function Select({ label, value, options, onChange, disabled = false }) {
  return (
    <label className="flex items-center gap-1">
      {label}
      <select
        className="field"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
