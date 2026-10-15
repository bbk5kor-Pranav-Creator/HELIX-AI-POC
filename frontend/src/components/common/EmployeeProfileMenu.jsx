import { useEffect, useRef, useState } from 'react';
import { ChevronDown, LogOut } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { clearEmployeeSession, getEmployeeSession } from '../../utils/auth';

function EmployeeProfileMenu({
  placement = 'header',
  wrapperClassName = '',
  profileClassName = '',
  logoutClassName = 'logout-button',
}) {
  const navigate = useNavigate();
  const containerRef = useRef(null);
  const triggerRef = useRef(null);
  const [isOpen, setIsOpen] = useState(false);
  const session = getEmployeeSession();

  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }

    const handlePointerDown = (event) => {
      if (!containerRef.current?.contains(event.target)) {
        setIsOpen(false);
      }
    };
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        setIsOpen(false);
        triggerRef.current?.focus();
      }
    };

    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);

    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen]);

  const logout = () => {
    clearEmployeeSession();
    navigate('/login', { replace: true });
  };

  const profileContent = (
    <>
      <span className="employee-profile-label">
        <strong>Employee</strong>
        {placement === 'sidebar' && <span>Employee</span>}
      </span>
      {placement === 'header' && <ChevronDown size={15} aria-hidden="true" />}
    </>
  );

  const menu = isOpen && (
    <div
      className={`employee-account-menu employee-account-menu--${placement}`}
      role="menu"
    >
      <strong className="employee-account-name">Employee</strong>
      <span className="employee-account-role">Employee</span>
      <div className="employee-account-information">
        <span>Account</span>
        <strong>{session?.email}</strong>
      </div>
      <button
        className="employee-account-logout"
        type="button"
        role="menuitem"
        onClick={logout}
      >
        <LogOut size={15} />
        Log Out
      </button>
    </div>
  );

  if (placement === 'sidebar') {
    return (
      <div
        ref={containerRef}
        className={`employee-profile-menu employee-profile-menu--sidebar ${wrapperClassName}`}
      >
        <button
          ref={triggerRef}
          className={`employee-account-trigger ${profileClassName}`}
          type="button"
          aria-expanded={isOpen}
          aria-haspopup="menu"
          onClick={() => setIsOpen((open) => !open)}
        >
          {profileContent}
        </button>
        {menu}
        <button
          className={`employee-account-logout ${logoutClassName}`}
          type="button"
          onClick={logout}
        >
          <LogOut size={16} />
          Log Out
        </button>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="employee-profile-menu employee-profile-menu--header"
    >
      <button
        ref={triggerRef}
        className={`employee-account-trigger ${profileClassName}`}
        type="button"
        aria-expanded={isOpen}
        aria-haspopup="menu"
        onClick={() => setIsOpen((open) => !open)}
      >
        {profileContent}
      </button>
      {menu}
    </div>
  );
}

export default EmployeeProfileMenu;
