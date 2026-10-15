function AdminDashboard() {
  return (
    <div className="login-page">
      <header className="login-header">
        <div className="header-brand">
          <img src="/bosch-logo.png" alt="Bosch" className="bosch-logo-image" />

          <div className="header-divider" />

          <div className="platform-brand">
            <h1>Helix Agentic Platform</h1>
            <p>From operational realities to aligned SOPs, with AI</p>
          </div>
        </div>

        <div className="organization-pill">
          <span className="organization-dot" />
          Digital Enterprise Operations
        </div>
      </header>

      <main>
        <h1>Admin Dashboard</h1>
        <p>Helix AI V2</p>
      </main>
    </div>
  );
}

export default AdminDashboard;