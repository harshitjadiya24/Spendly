// Dark mode toggle
const html = document.documentElement;
const currentTheme = localStorage.getItem('theme') || 'light';

if (currentTheme === 'dark') {
    html.classList.add('dark');
}

document.querySelectorAll('.theme-option').forEach(el => {
    if (el.dataset.theme === currentTheme) {
        el.classList.add('active');
    }
    el.addEventListener('click', (e) => {
        e.preventDefault();
        const theme = el.dataset.theme;
        html.classList.toggle('dark', theme === 'dark');
        localStorage.setItem('theme', theme);
        document.querySelectorAll('.theme-option').forEach(o => o.classList.remove('active'));
        el.classList.add('active');
        if (window.innerWidth <= 768) {
            document.querySelector('.nav-submenu')?.classList.remove('open');
        }
    });
});

// Submenu toggle (click, not hover)
document.querySelectorAll('.nav-submenu-trigger').forEach(trigger => {
    trigger.addEventListener('click', (e) => {
        e.preventDefault();
        trigger.parentElement.classList.toggle('open');
    });
});

document.addEventListener('click', (e) => {
    if (!e.target.closest('.nav-submenu')) {
        document.querySelectorAll('.nav-submenu.open').forEach(el => el.classList.remove('open'));
    }
});

// Confirm popup
const confirmOverlay = document.getElementById('confirmOverlay');
const confirmMsg = document.getElementById('confirmMsg');
const confirmOk = document.getElementById('confirmOk');
const confirmCancel = document.getElementById('confirmCancel');
let confirmCallback = null;

if (confirmOverlay) {
    confirmCancel.addEventListener('click', closeConfirm);
    confirmOverlay.addEventListener('click', (e) => {
        if (e.target === confirmOverlay) closeConfirm();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && confirmOverlay.classList.contains('active')) closeConfirm();
    });
    confirmOk.addEventListener('click', () => {
        if (confirmCallback) confirmCallback();
        closeConfirm();
    });
}

function closeConfirm() {
    if (!confirmOverlay) return;
    confirmOverlay.classList.remove('active');
    confirmCallback = null;
}

function showConfirm(msg, callback) {
    if (!confirmOverlay) return;
    confirmMsg.textContent = msg;
    confirmCallback = callback;
    confirmOverlay.classList.add('active');
}

// Hamburger nav toggle
const navToggle = document.getElementById('navToggle');
const navLinks = document.getElementById('navLinks');

if (navToggle && navLinks) {
    navToggle.addEventListener('click', () => {
        navLinks.classList.toggle('open');
    });

    document.addEventListener('click', (e) => {
        if (!navToggle.contains(e.target) && !navLinks.contains(e.target)) {
            navLinks.classList.remove('open');
        }
    });
}

// Mobile dropdown toggle
const dropdownBtn = document.querySelector('.nav-dropdown-btn');
const dropdown = document.querySelector('.nav-dropdown');
if (dropdownBtn && dropdown) {
    dropdownBtn.addEventListener('click', (e) => {
        if (window.innerWidth <= 768) {
            e.preventDefault();
            dropdown.classList.toggle('open');
        }
    });
}
