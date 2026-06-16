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

const CHART_KEYS = {
  num_messages: "num_messages",
  num_unique_senders: "participants",
  duration_sec: "duration",
  start_time: "start_month",
};

export default function StatsPanel({ stats, onBucketClick, activeBucket }) {
  if (!stats) {
    return (
      <div className="p-4 text-sm text-slate-500">טוען סטטיסטיקה...</div>
    );
  }

  const charts = [
    { key: "num_messages", title: "מספר הודעות" },
    { key: "num_unique_senders", title: "משתתפים" },
    { key: "duration_sec", title: "משך (שעות)" },
    { key: "start_time", title: "חודש התחלה" },
  ];

  return (
    <div className="space-y-4 p-3 overflow-y-auto max-h-[40vh] border-b border-slate-200">
      <div className="grid grid-cols-2 gap-2 text-sm">
        <div className="bg-white rounded p-2 border">
          <div className="text-slate-500">סה״כ</div>
          <div className="font-semibold">{stats.total}</div>
        </div>
        <div className="bg-white rounded p-2 border">
          <div className="text-slate-500">לא מועילות</div>
          <div className="font-semibold text-amber-700">{stats.useless}</div>
        </div>
      </div>

      {charts.map(({ key, title }) => {
        const buckets = stats.histograms?.[key] || [];
        if (!buckets.length) return null;

        const labels = buckets.map((b) =>
          key === "duration_sec"
            ? `${(b.min / 3600).toFixed(1)}`
            : b.label
        );
        const data = {
          labels,
          datasets: [
            {
              label: title,
              data: buckets.map((b) => b.count),
              backgroundColor: buckets.map((b, i) =>
                activeBucket?.chart === key && activeBucket?.index === i
                  ? "rgba(245, 158, 11, 0.8)"
                  : "rgba(59, 130, 246, 0.6)"
              ),
            },
          ],
        };

        return (
          <div key={key} className="bg-white rounded border p-2">
            <div className="text-xs font-medium mb-1 text-slate-600">{title}</div>
            <Bar
              data={data}
              options={{
                responsive: true,
                plugins: { legend: { display: false } },
                scales: {
                  x: { ticks: { maxRotation: 45, font: { size: 9 } } },
                  y: { beginAtZero: true, ticks: { precision: 0 } },
                },
                onClick: (_, elements) => {
                  if (!elements.length) return;
                  const idx = elements[0].index;
                  const bucket = buckets[idx];
                  onBucketClick(key, bucket, idx);
                },
              }}
              height={80}
            />
          </div>
        );
      })}
    </div>
  );
}

export { CHART_KEYS };
