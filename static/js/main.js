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
