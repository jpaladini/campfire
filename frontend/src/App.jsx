import React, { useCallback, useEffect, useRef, useState } from 'react'
import * as api from './api.js'

const CATS = [
  { key: 'win', label: 'Win', sub: 'What went right', icon: '✳', color: '#7a8450', placeholder: 'Add a win…' },
  { key: 'loss', label: 'Loss', sub: "What didn't", icon: '◟', color: '#b06a4a', placeholder: 'Add a loss…' },
  { key: 'help', label: 'Help', sub: 'What you need', icon: '☞', color: '#b8912f', placeholder: 'Add a help…' },
  { key: 'learned', label: 'Learned', sub: 'What you now know', icon: '❏', color: '#6b6555', placeholder: 'Add a learned…' },
]
const CAT_INFO = Object.fromEntries(CATS.map((c) => [c.key, c]))
const PERIODS = ['Day', 'Week', 'Month', 'Year']
const FILTERS = ['All', 'Wins', 'Losses', 'Help', 'Learned']
const FILTER_MAP = { Wins: 'win', Losses: 'loss', Help: 'help', Learned: 'learned' }

const emptyFields = () => ({
  win: [{ text: '', flash: false }],
  loss: [{ text: '', flash: false }],
  help: [{ text: '', flash: false }],
  learned: [{ text: '', flash: false }],
})

function timeAgo(iso) {
  const then = new Date(iso)
  const now = new Date()
  const mins = Math.floor((now - then) / 60000)
  if (mins < 2) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const startOfDay = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  if (then >= startOfDay) return 'today'
  if (then >= new Date(startOfDay - 86400000)) return 'yesterday'
  const daysSinceMonday = (now.getDay() + 6) % 7
  const monday = new Date(startOfDay.getTime() - daysSinceMonday * 86400000)
  if (then >= monday) return then.toLocaleDateString(undefined, { weekday: 'short' })
  if (then >= new Date(monday.getTime() - 7 * 86400000)) return 'last week'
  return then.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export default function App() {
  const [me, setMe] = useState(null)
  const [period, setPeriod] = useState('Week')
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settings, setSettings] = useState(null)
  const [fields, setFields] = useState(emptyFields)
  const [feed, setFeed] = useState([])
  const [stats, setStats] = useState({ wins: 0, losses: 0, helpOpen: 0, learnings: 0 })
  const [digest, setDigest] = useState({ range: '', text: '' })
  const [shipping, setShipping] = useState(null)
  const [filter, setFilter] = useState('All')
  const [justSaved, setJustSaved] = useState(false)
  const [saving, setSaving] = useState(false)
  const timers = useRef([])

  useEffect(() => () => timers.current.forEach(clearTimeout), [])

  useEffect(() => {
    api.getMe().then(setMe).catch(console.error)
    api.getSettings().then(setSettings).catch(console.error)
    api.getShipping().then(setShipping).catch(console.error)
  }, [])

  const refresh = useCallback((p) => {
    api.getFeed(p).then(({ entries, stats }) => { setFeed(entries); setStats(stats) }).catch(console.error)
    api.getDigest(p).then(setDigest).catch(console.error)
  }, [])

  useEffect(() => { refresh(period) }, [period, refresh])

  const setField = (key, i, patch) =>
    setFields((f) => ({ ...f, [key]: f[key].map((it, j) => (j === i ? { ...it, ...patch } : it)) }))

  const addField = (key) =>
    setFields((f) => ({ ...f, [key]: [...f[key], { text: '', flash: false }] }))

  const cleanField = async (key, i, text) => {
    try {
      const { text: cleaned } = await api.postCleanup(text)
      setField(key, i, { text: cleaned, flash: true })
      timers.current.push(setTimeout(() => setField(key, i, { flash: false }), 2200))
    } catch (e) {
      console.error(e)
    }
  }

  const itemCount = Object.values(fields).flat().filter((it) => it.text.trim()).length

  const onSave = async () => {
    const entries = CATS.flatMap((c) =>
      fields[c.key].filter((it) => it.text.trim()).map((it) => ({ category: c.key, text: it.text.trim() }))
    )
    if (!entries.length || saving) return
    setSaving(true)
    try {
      await api.postEntries(entries)
      setFields(emptyFields())
      setJustSaved(true)
      timers.current.push(setTimeout(() => setJustSaved(false), 2500))
      refresh(period)
    } catch (e) {
      console.error(e)
    } finally {
      setSaving(false)
    }
  }

  const updateSetting = (patch) => {
    const next = { ...settings, ...patch }
    setSettings(next)
    api.putSettings(next).catch(console.error)
  }

  const openCount = feed.filter((e) => e.category === 'help' && e.open).length
  const shownFeed = feed.filter((e) => filter === 'All' || e.category === FILTER_MAP[filter])

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <div className="flame" aria-hidden="true">
            <div className="flame-outer" /><div className="flame-inner" />
            <div className="flame-log1" /><div className="flame-log2" />
          </div>
          <span className="wordmark">Campfire</span>
        </div>
        <nav className="periods">
          {PERIODS.map((p) => (
            <button key={p} className={`period-btn${p === period ? ' active' : ''}`} onClick={() => setPeriod(p)}>
              {p}
            </button>
          ))}
        </nav>
        <div className="header-right">
          <div className="identity">
            <span className="avatar" style={{ background: me?.avatarColor || '#7a8450' }}>{me?.initials || '·'}</span>
            <span className="identity-name">{me?.name || '…'}</span>
            <span className="identity-via">via Databricks</span>
          </div>
          <button
            className={`settings-btn${settingsOpen ? ' open' : ''}`}
            title="Settings"
            onClick={() => setSettingsOpen((o) => !o)}
          >⚙</button>
        </div>
      </header>

      {settingsOpen && settings && (
        <div className="settings-strip">
          <span className="settings-label">SETTINGS</span>
          <label className="settings-check">
            <input type="checkbox" checked={settings.reminders} onChange={(e) => updateSetting({ reminders: e.target.checked })} />
            Weekly check-in reminder
          </label>
          <label className="settings-check">
            <input type="checkbox" checked={settings.autoClean} onChange={(e) => updateSetting({ autoClean: e.target.checked })} />
            Auto-clean on save
          </label>
          <label className="settings-check">
            <input type="checkbox" checked={settings.anonymousLosses} onChange={(e) => updateSetting({ anonymousLosses: e.target.checked })} />
            Post losses anonymously
          </label>
          <div className="settings-digest">
            Digest lands on
            <select value={settings.digestDay} onChange={(e) => updateSetting({ digestDay: e.target.value })}>
              <option>Monday</option><option>Wednesday</option><option>Friday</option>
            </select>
          </div>
        </div>
      )}

      <div className="main">
        <section className="form-col">
          {CATS.map((cat) => (
            <div className="cat-group" key={cat.key}>
              <div className="cat-head">
                <span style={{ color: cat.color }}>{cat.icon}</span>
                <span className="cat-label">{cat.label}</span>
                <span className="cat-sub">{cat.sub}</span>
              </div>
              {fields[cat.key].map((item, i) => (
                <div className="field-wrap" key={i}>
                  <div className="field-box">
                    <textarea
                      rows={2}
                      value={item.text}
                      placeholder={cat.placeholder}
                      onChange={(e) => setField(cat.key, i, { text: e.target.value, flash: false })}
                    />
                    {item.text.trim().length > 3 && (
                      <button className="clean-btn" title="Clean up with AI" onClick={() => cleanField(cat.key, i, item.text)}>✦</button>
                    )}
                  </div>
                  {item.flash && <span className="flash-tag">✦ cleaned up</span>}
                </div>
              ))}
              <a className="add-link" href="#add" onClick={(e) => { e.preventDefault(); addField(cat.key) }}>
                + Add another
              </a>
            </div>
          ))}
          <div className="save-row">
            <button className="save-btn" onClick={onSave} disabled={saving}>
              Save this {period.toLowerCase()}
            </button>
            <span className="item-count">{itemCount} items added</span>
          </div>
          {justSaved && <div className="saved-banner">✦ Saved — added to the team log</div>}
        </section>

        <section className="pulse-col">
          <div className="digest-card">
            <div className="digest-eyebrow">✦ AI DIGEST · {digest.range}</div>
            <p className="digest-text">{digest.text}</p>
          </div>

          {shipping && (
            <div className="shipping-card">
              <div className="shipping-head">
                <div className="shipping-eyebrow">✦ NOW SHIPPING</div>
                <span className="shipping-sync">
                  auto-summarized from Git &amp; work items · synced {shipping.syncedMinutesAgo}m ago
                </span>
              </div>
              <div className="shipping-rows">
                {shipping.rows.map((row, i) => (
                  <div className="shipping-row" key={i}>
                    <span className="shipping-dot" style={{ color: row.color }}>●</span>
                    <span><strong>{row.project}</strong> — {row.status}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="stats-row">
            {[
              { n: stats.wins, label: 'Wins', color: '#7a8450' },
              { n: stats.losses, label: 'Losses', color: '#b06a4a' },
              { n: stats.helpOpen, label: 'Help open', color: '#b8912f' },
              { n: stats.learnings, label: 'Learnings', color: '#6b6555' },
            ].map((s) => (
              <div className="stat-card" key={s.label}>
                <div className="stat-n" style={{ color: s.color }}>{s.n}</div>
                <div className="stat-label">{s.label}</div>
              </div>
            ))}
          </div>

          <div className="log-head">
            <div className="log-title">
              <span className="log-diamond">◆</span>
              <span className="log-name">Team log</span>
            </div>
            <div className="filters">
              {FILTERS.map((f) => (
                <button
                  key={f}
                  className={`filter-btn${filter === f ? ' active' : ''}`}
                  onClick={() => setFilter(f)}
                >
                  {f === 'Help' && openCount ? `Help · ${openCount}` : f}
                </button>
              ))}
            </div>
          </div>

          <div className="feed">
            {shownFeed.map((e) => (
              <div className="entry-card" key={e.id}>
                <div className="entry-avatar" style={{ background: e.avatarColor }}>{e.initials}</div>
                <div className="entry-body">
                  <div className="entry-meta">
                    {e.author} · <span className="entry-cat" style={{ color: CAT_INFO[e.category].color }}>{CAT_INFO[e.category].label}</span> · {timeAgo(e.createdAt)}
                    {e.category === 'help' && e.open && <span className="entry-open"> · open</span>}
                  </div>
                  <div className="entry-text">{e.text}</div>
                </div>
              </div>
            ))}
            {!shownFeed.length && (
              <div className="feed-empty">No entries for this {period.toLowerCase()} yet — be the first to check in.</div>
            )}
          </div>
        </section>
      </div>
    </div>
  )
}
