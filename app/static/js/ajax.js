// ── LYNK AJAX HELPERS ──

async function ajaxPost(url) {
    try {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        return await res.json();
    } catch (e) {
        console.error('AJAX error:', e);
        return null;
    }
}

// ── REACTION / LIKE ──
function initReaction(btn, picker, postId) {
    let hoverTimer = null;

    // single click = like toggle
    btn.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        if (picker.classList.contains('open')) {
            picker.classList.remove('open');
            return;
        }
        const data = await ajaxPost(`/ajax/like/${postId}`);
        if (!data || !data.success) return;
        if (data.liked) {
            btn.classList.add('reacted');
            btn.innerHTML = `❤️ ${data.count}`;
        } else {
            btn.classList.remove('reacted');
            btn.innerHTML = `🤍 ${data.count}`;
        }
        btn.style.transform = 'scale(1.3)';
        setTimeout(() => btn.style.transform = '', 200);
    });

    // hover 600ms = open reaction picker
    btn.addEventListener('mouseenter', () => {
        hoverTimer = setTimeout(() => picker.classList.add('open'), 600);
    });
    btn.addEventListener('mouseleave', () => {
        clearTimeout(hoverTimer);
    });

    // emoji click
    picker.querySelectorAll('.reaction-emoji').forEach(span => {
        span.addEventListener('click', async (e) => {
            e.preventDefault();
            e.stopPropagation();
            const emoji = span.textContent.trim();
            const data = await ajaxPost(`/ajax/react/${postId}/${encodeURIComponent(emoji)}`);
            if (!data || !data.success) return;
            picker.classList.remove('open');
            if (data.removed || !data.emoji) {
                btn.classList.remove('reacted');
                btn.innerHTML = `🤍 ${data.count}`;
            } else {
                btn.classList.add('reacted');
                btn.innerHTML = `${data.emoji} ${data.count}`;
            }
            btn.style.transform = 'scale(1.3)';
            setTimeout(() => btn.style.transform = '', 200);
        });
    });
}

// ── FOLLOW ──
function initFollow(btn, username) {
    btn.addEventListener('click', async (e) => {
        e.preventDefault();
        btn.disabled = true;
        btn.style.opacity = '0.6';
        const data = await ajaxPost(`/ajax/follow/${username}`);
        btn.disabled = false;
        btn.style.opacity = '1';
        if (!data || !data.success) return;
        if (data.status === 'pending') {
            btn.textContent = 'Requested ⏳';
            btn.classList.add('btn-ghost');
        } else {
            btn.textContent = 'Follow';
            btn.classList.remove('btn-ghost');
        }
    });
}

// ── POLL ──
function initPoll(container, pollId) {
    container.querySelectorAll('.poll-option-btn').forEach(optBtn => {
        optBtn.addEventListener('click', async (e) => {
            e.preventDefault();
            const optionId = optBtn.dataset.optionId;
            const data = await ajaxPost(`/ajax/poll-vote/${pollId}/${optionId}`);
            if (!data || !data.success) return;
            const pollDiv = container.querySelector('.poll-options');
            pollDiv.innerHTML = '';
            data.results.forEach(r => {
                pollDiv.innerHTML += `
                <div style="margin-bottom:8px;">
                    <div style="display:flex;justify-content:space-between;font-size:13px;font-weight:600;margin-bottom:4px;">
                        <span>${r.text}</span>
                        <span style="color:var(--purple);">${r.pct}%</span>
                    </div>
                    <div style="height:8px;background:var(--surface2);border-radius:99px;overflow:hidden;">
                        <div style="height:100%;width:0%;background:linear-gradient(135deg,var(--purple),var(--pink));border-radius:99px;transition:width 0.6s ease;" data-width="${r.pct}%"></div>
                    </div>
                </div>`;
            });
            container.querySelector('.poll-total').textContent = `${data.total} votes`;
            setTimeout(() => {
                container.querySelectorAll('[data-width]').forEach(bar => {
                    bar.style.width = bar.dataset.width;
                });
            }, 50);
        });
    });
}

// ── CLOSE PICKERS ON OUTSIDE CLICK ──
document.addEventListener('click', () => {
    document.querySelectorAll('.reaction-picker').forEach(p => p.classList.remove('open'));
});

// ── AUTO INIT ──
document.addEventListener('DOMContentLoaded', () => {
    // reactions
    document.querySelectorAll('[data-react-post]').forEach(wrap => {
        const btn = wrap.querySelector('.reaction-btn');
        const picker = wrap.querySelector('.reaction-picker');
        if (btn && picker) initReaction(btn, picker, wrap.dataset.reactPost);
    });

    // follows
    document.querySelectorAll('[data-follow-user]').forEach(btn => {
        initFollow(btn, btn.dataset.followUser);
    });

    // polls
    document.querySelectorAll('[data-poll-id]').forEach(container => {
        initPoll(container, container.dataset.pollId);
    });
});