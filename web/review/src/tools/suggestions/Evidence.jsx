import { Bidi, formatCount, Ltr, stanceOf } from "@shell";
import { quoteParts } from "./suggestions.js";

// What the owner judges a suggestion by, from the wiki as it is now (GET
// /suggestions/{id} → evidence): for a mistake the quoted sentence highlighted in its
// paragraph and the claim groups it rests on; for a contradiction the two sides next to each
// other; for a structural suggestion the pages involved with their scope and size. A group
// shows its text, stance, supporters / opposers, dates and the page sentences that cite it.

function Said({ said }) {
  if (!said?.length) return <p className="text-xs text-slate-400">אף משפט בוויקי לא מצטט את הקבוצה.</p>;
  return (
    <ul className="space-y-1">
      {said.map((place) => (
        <li key={place.page_id} className="text-xs">
          <span className="text-slate-500">
            בדף „<Bidi>{place.title}</Bidi>”:
          </span>
          {place.sentences.map((sentence, index) => (
            <Bidi as="p" key={index} className="mt-0.5 border-s-2 border-slate-200 ps-2 text-slate-700">
              {sentence}
            </Bidi>
          ))}
        </li>
      ))}
    </ul>
  );
}

export function GroupCard({ group }) {
  if (group.missing) {
    return (
      <li className="rounded border border-slate-200 bg-white p-2 text-xs text-slate-400">
        הקבוצה <Ltr>{group.group_id}</Ltr> כבר לא בקובץ הקבוצות.
      </li>
    );
  }
  const stance = stanceOf(group.stance);
  return (
    <li className="space-y-2 rounded border border-slate-200 bg-white p-3">
      <Bidi as="p" className="leading-relaxed">
        {group.claim_text}
      </Bidi>
      <p className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
        <span className={`badge ${stance.badge}`}>{stance.label}</span>
        <span>{formatCount(group.supporters)} תומכים</span>
        <span className={group.opposers > 0 ? "text-rose-700" : ""}>{formatCount(group.opposers)} מתנגדים</span>
        <span>
          <Ltr>{group.first_date}</Ltr>
          {group.last_date !== group.first_date && (
            <>
              {" – "}
              <Ltr>{group.last_date}</Ltr>
            </>
          )}
        </span>
        {group.primary_title && (
          <span>
            הדף הראשי: <Bidi>„{group.primary_title}”</Bidi>
          </span>
        )}
        <Ltr className="text-slate-400">{group.group_id}</Ltr>
      </p>
      <Said said={group.said_in} />
    </li>
  );
}

function Groups({ groups }) {
  return (
    <ul className="space-y-2">
      {groups.map((group) => (
        <GroupCard key={group.group_id} group={group} />
      ))}
    </ul>
  );
}

export function PageCard({ page }) {
  if (!page.exists) {
    return (
      <li className="rounded border border-rose-200 bg-rose-50 p-3 text-rose-800">
        הדף <Ltr>{page.page_id}</Ltr> אינו בתוכנית עכשיו.
      </li>
    );
  }
  return (
    <li className="space-y-1 rounded border border-slate-200 bg-white p-3">
      <p className="flex flex-wrap items-baseline gap-2">
        <Bidi className="font-medium">{page.title}</Bidi>
        <Ltr className="text-xs text-slate-400">{page.page_id}</Ltr>
        {page.path.length > 0 && <Bidi className="text-xs text-slate-500">{page.path.join(" › ")}</Bidi>}
      </p>
      <p className="text-xs text-slate-600">
        {formatCount(page.groups.main)} קבוצות שזה הדף הראשי שלהן · {formatCount(page.groups.mention)} שרק מוזכרות בו ·{" "}
        {page.written ? `${formatCount(page.words)} מילים` : "הדף עוד לא נכתב"}
      </p>
      <Bidi as="p" className="text-xs text-slate-500">
        {page.description}
      </Bidi>
      <details className="text-xs">
        <summary className="cursor-pointer text-slate-500">התחום</summary>
        <Bidi as="p" className="mt-1 whitespace-pre-line leading-relaxed text-slate-700">
          {page.scope}
        </Bidi>
      </details>
    </li>
  );
}

export function Pages({ pages }) {
  return (
    <ul className="space-y-2">
      {pages.map((page) => (
        <PageCard key={page.page_id} page={page} />
      ))}
    </ul>
  );
}

function Quote({ quote, sentence }) {
  if (!quote) {
    return (
      <div className="space-y-1 rounded border border-amber-200 bg-amber-50 p-3 text-amber-900">
        <p>המשפט כבר לא נמצא בדף כפי שהוא עכשיו (הדף השתנה מאז הסקירה, או שההצעה כבר בוצעה). המשפט שהסקירה ציטטה:</p>
        <Bidi as="p" className="leading-relaxed">
          {sentence}
        </Bidi>
      </div>
    );
  }
  const [before, quoted, after] = quoteParts(quote);
  return (
    <Bidi as="p" className="rounded border border-slate-200 bg-white p-3 leading-relaxed">
      {before}
      <mark className="rounded bg-amber-200 px-0.5 text-slate-900">{quoted}</mark>
      {after}
    </Bidi>
  );
}

function Section({ title, children }) {
  return (
    <section>
      <h3 className="section-title">{title}</h3>
      {children}
    </section>
  );
}

function MistakeEvidence({ evidence, proposal }) {
  return (
    <>
      <Section title="המשפט בדף, כפי שהדף נראה עכשיו">
        <Quote quote={evidence.quote} sentence={proposal.sentence} />
      </Section>
      <Section title="הקבוצות שההצעה נשענת עליהן">
        <Groups groups={evidence.groups} />
      </Section>
      <Section title="הדף">
        <Pages pages={[evidence.page]} />
      </Section>
    </>
  );
}

function ContradictionEvidence({ evidence }) {
  return (
    <>
      <Section title="שני הצדדים">
        <div className="grid gap-3 lg:grid-cols-2">
          <div>
            <p className="mb-1 text-xs font-medium text-slate-600">צד א</p>
            <Groups groups={evidence.sides.a} />
          </div>
          <div>
            <p className="mb-1 text-xs font-medium text-slate-600">צד ב</p>
            <Groups groups={evidence.sides.b} />
          </div>
        </div>
      </Section>
      <Section title="הדפים שצריכים להציג את שני הצדדים">
        <Pages pages={evidence.pages} />
      </Section>
    </>
  );
}

function StructuralEvidence({ evidence }) {
  return (
    <>
      <Section title="הדפים שההצעה נוגעת בהם">
        {evidence.pages.length ? <Pages pages={evidence.pages} /> : <p className="text-slate-400">ההצעה לא מזכירה דפים קיימים.</p>}
      </Section>
      {evidence.parent && (
        <Section title={<>הדפים שכבר יש בקטגוריה „<Bidi>{evidence.parent.title}</Bidi>”</>}>
          {evidence.parent.pages.length ? <Pages pages={evidence.parent.pages} /> : <p className="text-slate-400">אין בה דפים.</p>}
        </Section>
      )}
      {evidence.labels && (
        <Section title="התוויות הקיימות מאותו סוג">
          {evidence.labels.length ? (
            <ul className="space-y-1">
              {evidence.labels.map((label) => (
                <li key={label.label_id} className="rounded border border-slate-200 bg-white p-2 text-xs">
                  <Bidi className="font-medium">{label.title}</Bidi> <Ltr className="text-slate-400">{label.label_id}</Ltr>
                  <Bidi as="p" className="text-slate-600">
                    {label.description}
                  </Bidi>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-slate-400">אין עדיין תוויות מהסוג הזה.</p>
          )}
        </Section>
      )}
    </>
  );
}

export function Evidence({ suggestion, evidence, proposal }) {
  if (suggestion.kind === "mistake") return <MistakeEvidence evidence={evidence} proposal={proposal} />;
  if (suggestion.kind === "contradiction") return <ContradictionEvidence evidence={evidence} />;
  return <StructuralEvidence evidence={evidence} />;
}
