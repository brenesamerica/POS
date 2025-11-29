/**
 * API utilities for POS application (TPL-005)
 * Provides consistent fetch wrappers with error handling
 */

/**
 * Make a JSON API request
 * @param {string} url - API endpoint URL
 * @param {Object} options - Fetch options
 * @param {string} [options.method='GET'] - HTTP method
 * @param {Object} [options.body] - Request body (will be JSON stringified)
 * @param {Object} [options.headers] - Additional headers
 * @returns {Promise<Object>} Response data
 * @throws {Error} On network or API error
 */
async function apiRequest(url, options = {}) {
    const { method = 'GET', body, headers = {} } = options;

    const config = {
        method,
        headers: {
            'Content-Type': 'application/json',
            ...headers
        }
    };

    if (body && method !== 'GET') {
        config.body = JSON.stringify(body);
    }

    try {
        const response = await fetch(url, config);
        const data = await response.json();

        if (!response.ok) {
            throw new ApiError(
                data.message || `HTTP ${response.status}`,
                response.status,
                data
            );
        }

        return data;
    } catch (error) {
        if (error instanceof ApiError) {
            throw error;
        }
        throw new ApiError(error.message || 'Network error', 0, null);
    }
}

/**
 * Custom API error class
 */
class ApiError extends Error {
    constructor(message, status, data) {
        super(message);
        this.name = 'ApiError';
        this.status = status;
        this.data = data;
    }
}

/**
 * POST request helper
 * @param {string} url - API endpoint
 * @param {Object} body - Request body
 * @returns {Promise<Object>} Response data
 */
async function apiPost(url, body) {
    return apiRequest(url, { method: 'POST', body });
}

/**
 * GET request helper
 * @param {string} url - API endpoint
 * @returns {Promise<Object>} Response data
 */
async function apiGet(url) {
    return apiRequest(url, { method: 'GET' });
}

/**
 * DELETE request helper
 * @param {string} url - API endpoint
 * @returns {Promise<Object>} Response data
 */
async function apiDelete(url) {
    return apiRequest(url, { method: 'DELETE' });
}

/**
 * Show a toast notification
 * @param {string} message - Message to display
 * @param {string} [type='info'] - Type: 'success', 'error', 'warning', 'info'
 * @param {number} [duration=3000] - Duration in ms
 */
function showToast(message, type = 'info', duration = 3000) {
    const colors = {
        success: '#10b981',
        error: '#ef4444',
        warning: '#f59e0b',
        info: '#3b82f6'
    };

    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.style.cssText = `
        position: fixed;
        bottom: 80px;
        left: 50%;
        transform: translateX(-50%);
        padding: 12px 24px;
        border-radius: 8px;
        color: white;
        font-weight: 500;
        z-index: 9999;
        background: ${colors[type] || colors.info};
        animation: slideUp 0.3s ease;
    `;
    toast.textContent = message;

    document.body.appendChild(toast);

    setTimeout(() => {
        toast.style.animation = 'slideDown 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, duration);
}

/**
 * Format price in HUF
 * @param {number} amount - Amount to format
 * @returns {string} Formatted price string
 */
function formatPrice(amount) {
    return new Intl.NumberFormat('hu-HU', {
        style: 'currency',
        currency: 'HUF',
        minimumFractionDigits: 0,
        maximumFractionDigits: 0
    }).format(amount);
}

/**
 * Format date/time
 * @param {string|Date} date - Date to format
 * @param {Object} [options] - Intl.DateTimeFormat options
 * @returns {string} Formatted date string
 */
function formatDateTime(date, options = {}) {
    const defaultOptions = {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit'
    };
    return new Intl.DateTimeFormat('hu-HU', { ...defaultOptions, ...options })
        .format(new Date(date));
}

/**
 * Debounce function calls
 * @param {Function} func - Function to debounce
 * @param {number} wait - Wait time in ms
 * @returns {Function} Debounced function
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * Confirm action with dialog
 * @param {string} message - Confirmation message
 * @returns {Promise<boolean>} True if confirmed
 */
async function confirmAction(message) {
    return new Promise(resolve => {
        resolve(window.confirm(message));
    });
}

// Add CSS keyframes for toast animation if not present
if (!document.getElementById('api-js-styles')) {
    const style = document.createElement('style');
    style.id = 'api-js-styles';
    style.textContent = `
        @keyframes slideUp {
            from { transform: translate(-50%, 20px); opacity: 0; }
            to { transform: translate(-50%, 0); opacity: 1; }
        }
        @keyframes slideDown {
            from { transform: translate(-50%, 0); opacity: 1; }
            to { transform: translate(-50%, 20px); opacity: 0; }
        }
    `;
    document.head.appendChild(style);
}
