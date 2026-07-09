// Video modal (landing page only)
const modal = document.getElementById('videoModal');
const openBtn = document.getElementById('howItWorksBtn');
const closeBtn = document.getElementById('closeModal');

if (openBtn && modal) {
    openBtn.addEventListener('click', () => {
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
    });

    closeBtn.addEventListener('click', closeModal);
    modal.addEventListener('click', (e) => {
        if (e.target === modal) closeModal();
    });

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && modal.classList.contains('active')) closeModal();
    });
}

function closeModal() {
    if (!modal) return;
    modal.classList.remove('active');
    document.body.style.overflow = '';
    const iframe = modal.querySelector('iframe');
    if (iframe) iframe.src = iframe.src;
}

// Dark mode toggle
const html = document.documentElement;
const toggle = document.getElementById('themeToggle');

if (localStorage.getItem('theme') === 'dark') {
    html.classList.add('dark');
}

if (toggle) {
    toggle.addEventListener('click', () => {
        html.classList.toggle('dark');
        localStorage.setItem('theme', html.classList.contains('dark') ? 'dark' : 'light');
    });
}

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
