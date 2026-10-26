
import { useState } from 'react'
import {
  LayoutDashboard,
  FileText,
  CreditCard,
  FolderArchive,
  Bot,
  Settings,
  Search,
  Bell,
  Plus,
  ArrowUpRight,
  Clock3,
  CircleCheck,
  AlertCircle,
  Menu,
} from 'lucide-react'

const navigation = [
  { label: 'Vue d’ensemble', icon: LayoutDashboard },
  { label: 'Mes factures', icon: FileText },
  { label: 'Paiements', icon: CreditCard },
  { label: 'Comptabilité', icon: FolderArchive },
  { label: 'Assistant IA', icon: Bot },
]

const demoInvoices = [
  { supplier: 'Entreprise Dupont', number: 'FA-2026-118', date: '12 oct. 2026', amount: '2 100,00 €', status: 'À payer', tone: 'pending' },
  { supplier: 'Ocean Supplies', number: 'INV-9923', date: '10 oct. 2026', amount: '462,00 $', status: 'Partielle', tone: 'partial' },
  { supplier: 'Atelier Créole', number: '2026-307', date: '3 oct. 2026', amount: '347,74 €', status: 'À vérifier', tone: 'review' },
]

function App() {
  const [activePage, setActivePage] = useState('Vue d’ensemble')
  const [search, setSearch] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)

  const filteredInvoices = demoInvoices.filter((invoice) =>
    `${invoice.supplier} ${invoice.number} ${invoice.status}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  )

  return (
    <div className="app-shell">
      <aside className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`}>
        <div className="brand">
          <div className="brand-mark">F<span>✦</span></div>
          <div>
            <strong>FACTURE <span>IA</span></strong>
            <small>GESTION INTELLIGENTE</small>
          </div>
        </div>

        <div className="workspace-label">ESPACE DE TRAVAIL</div>

        <nav className="navigation">
          {navigation.map(({ label, icon: Icon }) => (
            <button
              key={label}
              className={`nav-item ${activePage === label ? 'active' : ''}`}
              onClick={() => {
                setActivePage(label)
                setMenuOpen(false)
              }}
            >
              <Icon size={19} />
              <span>{label}</span>
              {label === 'Mes factures' && <span className="nav-count">03</span>}
            </button>
          ))}
        </nav>

        <div className="sidebar-bottom">
          <div className="storage-card">
            <div className="storage-icon"><FolderArchive size={19} /></div>
            <strong>Votre espace documentaire</strong>
            <p>Retrouvez vos documents et préparez vos exports comptables.</p>
          </div>
          <button className="nav-item settings-item" onClick={() => setActivePage('Paramètres')}>
            <Settings size={19} /><span>Paramètres</span>
          </button>
          <div className="profile">
            <div className="avatar">FA</div>
            <div><strong>Mon espace</strong><small>Compte de démonstration</small></div>
          </div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <button className="icon-button mobile-menu" aria-label="Ouvrir le menu" onClick={() => setMenuOpen(!menuOpen)}>
            <Menu size={20} />
          </button>
          <div className="breadcrumb">Espace de travail <span>/</span> <strong>{activePage}</strong></div>
          <div className="topbar-actions">
            <label className="search-box">
              <Search size={17} />
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Rechercher une facture..." />
              <kbd>⌘ K</kbd>
            </label>
            <button className="icon-button notification-button" aria-label="Notifications"><Bell size={19} /><i /></button>
            <div className="top-avatar">FA</div>
          </div>
        </header>

        <div className="page-content">
          <section className="welcome-row">
            <div>
              <div className="eyebrow"><span /> VOTRE GESTION, SIMPLIFIÉE</div>
              <h1>{activePage === 'Vue d’ensemble' ? 'Bonjour et bienvenue.' : activePage}</h1>
              <p className="subtitle">Gérez vos factures, suivez vos paiements et préparez votre comptabilité en toute simplicité.</p>
            </div>
            <button className="primary-button" onClick={() => setActivePage('Importer une facture')}>
              <Plus size={18} /> Nouvelle facture
            </button>
          </section>

          <div className="demo-notice">
            <AlertCircle size={17} />
            <span><strong>Mode démonstration</strong> — ces données sont fictives. Aucune facture réelle n’est connectée.</span>
          </div>

          <section className="stats-grid">
            <article className="stat-card">
              <div className="stat-top"><span>Factures enregistrées</span><div className="stat-icon"><FileText size={19} /></div></div>
              <div className="stat-value">03</div>
              <div className="stat-foot">Exemples de démonstration</div>
            </article>
            <article className="stat-card">
              <div className="stat-top"><span>Montant à régler</span><div className="stat-icon gold-icon"><CreditCard size={19} /></div></div>
              <div className="stat-value">2 447,74 €</div>
              <div className="stat-foot">Valeurs fictives, devises non converties</div>
            </article>
            <article className="stat-card">
              <div className="stat-top"><span>À vérifier</span><div className="stat-icon warning-icon"><AlertCircle size={19} /></div></div>
              <div className="stat-value">01</div>
              <div className="stat-foot">Contrôle humain recommandé</div>
            </article>
            <article className="stat-card">
              <div className="stat-top"><span>Échéances à venir</span><div className="stat-icon success-icon"><Clock3 size={19} /></div></div>
              <div className="stat-value">02</div>
              <div className="stat-foot">Exemples uniquement</div>
            </article>
          </section>

          <section className="content-grid">
            <article className="panel invoice-panel">
              <div className="panel-heading">
                <div><h2>Factures récentes</h2><p>Un aperçu des documents de démonstration</p></div>
                <button className="text-button" onClick={() => setActivePage('Mes factures')}>Voir les factures <ArrowUpRight size={15} /></button>
              </div>

              <div className="invoice-table-wrap">
                <table className="invoice-table">
                  <thead><tr><th>FOURNISSEUR</th><th>RÉFÉRENCE</th><th>DATE</th><th>MONTANT</th><th>STATUT</th></tr></thead>
                  <tbody>
                    {filteredInvoices.map((invoice) => (
                      <tr key={invoice.number}>
                        <td><strong>{invoice.supplier}</strong></td>
                        <td className="muted-cell">{invoice.number}</td>
                        <td className="muted-cell">{invoice.date}</td>
                        <td className="amount-cell">{invoice.amount}</td>
                        <td><span className={`status-pill ${invoice.tone}`}>{invoice.status}</span></td>
                      </tr>
                    ))}
                    {filteredInvoices.length === 0 && <tr><td colSpan="5" className="empty-state">Aucune facture trouvée.</td></tr>}
                  </tbody>
                </table>
              </div>
            </article>

            <article className="panel assistant-panel">
              <div className="assistant-orb"><Bot size={24} /></div>
              <div className="assistant-tag">ASSISTANT FACTURE IA</div>
              <h2>Votre gestion, plus claire.</h2>
              <p>Préparez vos questions sur les factures, les échéances et les paiements. L’assistant réel sera connecté lors de l’intégration du backend.</p>
              <button className="secondary-button" onClick={() => setActivePage('Assistant IA')}>Découvrir l’assistant <ArrowUpRight size={16} /></button>
              <div className="assistant-footer"><CircleCheck size={15} /> Aucune donnée réelle utilisée dans cette maquette</div>
            </article>
          </section>

          <footer className="page-footer">
            <span>© 2026 Facture IA</span>
            <span>Interface en cours de préparation <span className="footer-dot">•</span> Backend non connecté</span>
          </footer>
        </div>
      </main>
    </div>
  )
}

export default App
