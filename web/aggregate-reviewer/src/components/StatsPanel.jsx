import { useState } from "react";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  Title,
  Tooltip,
  Legend,
} from "chart.js";
import { Bar } from "react-chartjs-2";

ChartJS.register(CategoryScale, LinearScale, BarElement, Title, Tooltip, Legend);

function bucketLabel(bucket) {
  const size = bucket.label ?? String(bucket.size);
  if (size === "1") return "1";
  return size;
}

export default function StatsPanel({ stats, activeSizeFilter, onSizeClick }) {
  const [tableOpen, setTableOpen] = useState(true);

  if (!stats) {
    return (
      <div className="px-4 py-3 text-sm text-slate-500 border-b border-slate-200">
        טוען סטטיסטיקה...
      </div>
    );
  }

  const buckets = stats.cluster_size || [];
  const activeIndex = buckets.findIndex(
    (b) => activeSizeFilter && activeSizeFilter.size === b.size
  );
  const chartMinWidth = Math.max(480, buckets.length * 44);

  if (buckets.length === 0) {
    return (
      <div className="px-4 py-3 border-b border-slate-200 bg-white shrink-0 text-sm text-slate-500">
        אין קבוצות להצגה
      </div>
    );
  }

  const chartData = {
    labels: buckets.map(bucketLabel),
    datasets: [
      {
        label: "מספר קבוצות",
        data: buckets.map((b) => b.count),
        backgroundColor: buckets.map((b, i) =>
          i === activeIndex
            ? "rgba(20, 184, 166, 0.85)"
            : "rgba(59, 130, 246, 0.65)"
        ),
        borderColor: buckets.map((_, i) =>
          i === activeIndex ? "rgb(13, 148, 136)" : "rgb(148, 163, 184)"
        ),
        borderWidth: 1,
      },
    ],
  };

  return (
    <div className="px-4 py-3 border-b border-slate-200 bg-white shrink-0">
      <div className="flex flex-wrap gap-4 text-sm mb-3">
        <div>
          <span className="text-slate-500">נושאים: </span>
          <span className="font-semibold">{stats.topic_count}</span>
        </div>
        <div>
          <span className="text-slate-500">קבוצות: </span>
          <span className="font-semibold">{stats.group_count}</span>
        </div>
        <div>
          <span className="text-slate-500">טענות מקור: </span>
          <span className="font-semibold">{stats.source_claim_count}</span>
        </div>
        <div>
          <span className="text-slate-500">יחידים: </span>
          <span className="font-semibold">{stats.singleton_count}</span>
        </div>
        {stats.max_cluster_size != null && (
          <div>
            <span className="text-slate-500">מקס׳ בקבוצה: </span>
            <span className="font-semibold">{stats.max_cluster_size}</span>
          </div>
        )}
      </div>

      <div className="text-xs font-medium text-slate-600 mb-2">
        התפלגות גודל קבוצה — עמודה לכל מספר טענות (לחץ לסינון)
      </div>

      <div className="overflow-x-auto pb-1">
        <div style={{ minWidth: chartMinWidth, height: 140 }}>
          <Bar
            data={chartData}
            options={{
              responsive: true,
              maintainAspectRatio: false,
              plugins: {
                legend: { display: false },
                tooltip: {
                  callbacks: {
                    title: (items) => {
                      const idx = items[0]?.dataIndex;
                      return buckets[idx]?.description || items[0]?.label;
                    },
                    label: (item) => `${item.raw} קבוצות`,
                  },
                },
              },
              scales: {
                x: {
                  ticks: { font: { size: 10 } },
                  title: {
                    display: true,
                    text: "מספר טענות בקבוצה",
                    font: { size: 11 },
                  },
                },
                y: {
                  beginAtZero: true,
                  ticks: { precision: 0, stepSize: 1 },
                  title: {
                    display: true,
                    text: "מספר קבוצות",
                    font: { size: 11 },
                  },
                },
              },
              onClick: (_, elements) => {
                if (!elements.length) return;
                const idx = elements[0].index;
                const bucket = buckets[idx];
                onSizeClick(
                  activeSizeFilter?.size === bucket.size ? null : bucket
                );
              },
            }}
          />
        </div>
      </div>

      <div className="mt-3">
        <button
          type="button"
          onClick={() => setTableOpen((open) => !open)}
          className="flex w-full items-center justify-between gap-2 text-xs font-medium text-slate-600 hover:text-slate-800 py-1"
          aria-expanded={tableOpen}
        >
          <span>פירוט לפי גודל קבוצה</span>
          <span className="text-slate-400" aria-hidden="true">
            {tableOpen ? "▾" : "◂"}
          </span>
        </button>

        {tableOpen && (
          <div className="overflow-x-auto mt-1">
            <table className="text-xs text-slate-700 border-collapse min-w-full">
              <thead>
                <tr className="border-b border-slate-200 text-slate-500">
                  <th className="py-1 px-2 text-right font-medium">טענות בקבוצה</th>
                  <th className="py-1 px-2 text-right font-medium">מספר קבוצות</th>
                  <th className="py-1 px-2 text-right font-medium">סינון</th>
                </tr>
              </thead>
              <tbody>
                {buckets.map((bucket) => {
                  const active = activeSizeFilter?.size === bucket.size;
                  return (
                    <tr
                      key={String(bucket.size)}
                      className={`border-b border-slate-100 ${active ? "bg-teal-50" : ""}`}
                    >
                      <td className="py-1 px-2">
                        {bucket.size === 1 ? "טענה אחת" : `${bucket.size} טענות`}
                      </td>
                      <td className="py-1 px-2 font-medium">{bucket.count}</td>
                      <td className="py-1 px-2">
                        <button
                          type="button"
                          onClick={() => onSizeClick(active ? null : bucket)}
                          className={`underline hover:text-teal-700 ${
                            active ? "font-semibold text-teal-700" : ""
                          }`}
                        >
                          {active ? "נקה" : "הצג"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {activeSizeFilter && (
        <button
          type="button"
          onClick={() => onSizeClick(null)}
          className="mt-2 text-xs text-slate-500 underline hover:text-slate-700"
        >
          נקה סינון ({activeSizeFilter.description})
        </button>
      )}
    </div>
  );
}
