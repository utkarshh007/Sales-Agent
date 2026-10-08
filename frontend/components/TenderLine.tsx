import Link from "next/link";
import { closesIn, daysUntil, inr, TYPE_LABEL } from "@/lib/format";
import type { TenderRow } from "@/lib/types";
import { ScoreBar, ScoreMark } from "./ui";

/** Compact tender entry used on the overview lists. */
export default function TenderLine({ t, note }: { t: TenderRow; note?: string }) {
  const d = daysUntil(t.closing_at);
  return (
    <li className="grid grid-cols-[4.5rem_1fr] gap-x-4 py-3">
      <div className="pt-0.5"><ScoreMark score={t.score} priority={t.priority} stacked /></div>
      <div className="min-w-0">
        <Link href={`/tenders/${t.id}`} className="font-serif text-[16px] leading-snug hover:text-teal hover:underline">
          {t.title}
        </Link>
        <p className="mt-0.5 text-sm text-muted">
          {t.organization ?? "Unknown organisation"}
          {" — "}
          <span className={d !== null && d < 7 ? "font-medium text-high" : ""}>{closesIn(t.closing_at)}</span>
        </p>
        <p className="mt-1 text-sm">
          {TYPE_LABEL[t.opportunity_type]}
          {t.matched_capability ? `, ${t.matched_capability}` : ""}
          {t.tender_value_inr ? `, ${inr(t.tender_value_inr)}` : ""}
        </p>
        {note && <p className="mt-1 text-sm text-review">{note}</p>}
        <div className="mt-2 max-w-sm"><ScoreBar parts={t.score_parts} height={5} /></div>
      </div>
    </li>
  );
}
