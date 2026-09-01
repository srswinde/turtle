document.addEventListener('DOMContentLoaded', () => {
	const dashboard = document.querySelector('.dashboard');
	const actionButtons = Array.from(document.querySelectorAll('.icon-btn'));
	const closeButtons = Array.from(document.querySelectorAll('[data-close]'));
	const lightToggleButton = document.querySelector('[data-light-toggle]');
	const lightStatusText = document.querySelector('[data-light-status]');
	const lightLabel = lightToggleButton ? lightToggleButton.querySelector('.device-toggle__label') : null;
	const pumpToggleButton = document.querySelector('[data-pump-toggle]');
	const pumpStatusText = document.querySelector('[data-pump-status]');
	const pumpLabel = pumpToggleButton ? pumpToggleButton.querySelector('.device-toggle__label') : null;
	const cassiniYesButton = document.querySelector('[data-cassini-yes]');
	const cassiniNoButton = document.querySelector('[data-cassini-no]');
	const shedStatusText = document.querySelector('[data-shed-status]');
	const shedTableBody = document.querySelector('[data-shed-rows]');
	const shedRefreshButton = document.querySelector('[data-shed-refresh]');
	const shedPreviewImage = document.querySelector('[data-shed-preview]');
	const shedPreviewPlaceholder = document.querySelector('[data-shed-placeholder]');
	const SHED_IMAGE_PREFIX = '/mnt/nfs/shed-cam/';
	const SHED_IMAGE_BASE = '/app/shed-cam/images/';

	const holeRefreshButton = document.querySelector('[data-hole-refresh]');
	const holeStatusText = document.querySelector('[data-hole-status]');
	const holePreviewImage = document.querySelector('[data-hole-preview]');
	const holePreviewPlaceholder = document.querySelector('[data-hole-placeholder]');
	const HOLE_IMAGE_PREFIX = '/mnt/nfs/hole-cam/';
	const HOLE_IMAGE_BASE = '/app/hole-cam/images';
	const wledShedStatusText = document.querySelector('[data-wled-shed-status]');
	const wledBookcaseStatusText = document.querySelector('[data-wled-bookcase-status]');
	const wledShedDetailText = document.querySelector('[data-wled-shed-detail]');
	const wledBookcaseDetailText = document.querySelector('[data-wled-bookcase-detail]');
	const wledShedPowerButton = document.querySelector('[data-wled-power="shed"]');
	const wledBookcasePowerButton = document.querySelector('[data-wled-power="bookcase"]');
	const wledShedWheel = document.querySelector('[data-wled-wheel="shed"]');
	const wledBookcaseWheel = document.querySelector('[data-wled-wheel="bookcase"]');
	const wledShedWheelWrap = document.querySelector('[data-wled-wheel-wrap="shed"]');
	const wledBookcaseWheelWrap = document.querySelector('[data-wled-wheel-wrap="bookcase"]');
	const wledShedWheelThumb = document.querySelector('[data-wled-wheel-thumb="shed"]');
	const wledBookcaseWheelThumb = document.querySelector('[data-wled-wheel-thumb="bookcase"]');
	const wledRefreshButton = document.querySelector('[data-wled-refresh]');

	const allowedShedLabels = new Set(['person', 'cat', 'dog']);
	const shedTimeFormatter = new Intl.DateTimeFormat(undefined, {
		dateStyle: 'short',
		timeStyle: 'medium',
	});
	const shedPreviewDefaultText = shedPreviewPlaceholder ? shedPreviewPlaceholder.textContent : 'Select a detection to view its snapshot.';
	const shedStatusDefaultText = shedStatusText ? shedStatusText.textContent : 'Awaiting latest detections…';
	const updateCassiniButtons = () => {
		const detectionReady = currentCassiniDetection && Number.isFinite(currentCassiniDetection.timestamp);
		console.log(currentCassiniDetection);
		const enabled = Boolean(detectionReady) && !cassiniSubmitting;
		[cassiniYesButton, cassiniNoButton].forEach((button) => {
			if (button) {
				button.disabled = !enabled;
			}
		});
	};

	const normalizeCassiniImageUrl = (raw) => {
		if (!raw) {
			return null;
		}
		const value = String(raw);
		if (value.startsWith('http://') || value.startsWith('https://')) {
			return value;
		}
		if (value.startsWith('/')) {
			return `https://swahle.org${value}`;
		}
		return `https://swahle.org/${value}`;
	};

	const extractCassiniTimestamp = (record) => {
		if (!record || typeof record !== 'object') {
			return null;
		}
		const candidates = [
			record.timestamp,
			record.ts,
			record.captured_at,
			record.created_at,
			record.time,
		];
		for (const value of candidates) {
			if (value == null || value === '') {
				continue;
			}
			if (value instanceof Date) {
				return value.getTime();
			}
			const numeric = typeof value === 'number' ? value : Number(value);
			if (Number.isFinite(numeric)) {
				return numeric > 1e12 ? Math.trunc(numeric) : Math.trunc(numeric * 1000);
			}
			const parsed = Date.parse(String(value));
			if (!Number.isNaN(parsed)) {
				return parsed;
			}
		}
		return null;
	};

	let lightBusy = false;
	let pumpBusy = false;
	let shedLoading = false;
	let shedDataCache = null;
	let shedDisplayCache = [];
	let shedSelectedRow = null;
	let shedMatchCount = 0;
	let holeLoading = false;
	let holeDataCache = null;
	let holeDisplayCache = [];
	let currentHoleDetection = null;
	let currentCassiniDetection = null;
	let cassiniSubmitting = false;
	const wledBusy = {
		shed: false,
		bookcase: false,
	};
	const wledColorTimers = {
		shed: null,
		bookcase: null,
	};
	const wledCurrentColor = {
		shed: '#ffffff',
		bookcase: '#ffffff',
	};

	const clamp = (value, min, max) => Math.min(Math.max(value, min), max);

	const hsvToRgb = (hue, saturation, value) => {
		const normalizedHue = ((hue % 360) + 360) % 360;
		const chroma = value * saturation;
		const x = chroma * (1 - Math.abs(((normalizedHue / 60) % 2) - 1));
		const m = value - chroma;
		let redPrime = 0;
		let greenPrime = 0;
		let bluePrime = 0;

		if (normalizedHue < 60) {
			redPrime = chroma;
			greenPrime = x;
		} else if (normalizedHue < 120) {
			redPrime = x;
			greenPrime = chroma;
		} else if (normalizedHue < 180) {
			greenPrime = chroma;
			bluePrime = x;
		} else if (normalizedHue < 240) {
			greenPrime = x;
			bluePrime = chroma;
		} else if (normalizedHue < 300) {
			redPrime = x;
			bluePrime = chroma;
		} else {
			redPrime = chroma;
			bluePrime = x;
		}

		return {
			r: Math.round((redPrime + m) * 255),
			g: Math.round((greenPrime + m) * 255),
			b: Math.round((bluePrime + m) * 255),
		};
	};

	const rgbToHex = (red, green, blue) => {
		const toHex = (value) => clamp(Math.round(value), 0, 255).toString(16).padStart(2, '0');
		return `#${toHex(red)}${toHex(green)}${toHex(blue)}`;
	};

	const hexToRgb = (hexColor) => {
		if (!/^#[0-9a-fA-F]{6}$/.test(hexColor)) {
			return null;
		}
		return {
			r: parseInt(hexColor.slice(1, 3), 16),
			g: parseInt(hexColor.slice(3, 5), 16),
			b: parseInt(hexColor.slice(5, 7), 16),
		};
	};

	const rgbToHsv = (red, green, blue) => {
		const redNorm = red / 255;
		const greenNorm = green / 255;
		const blueNorm = blue / 255;
		const max = Math.max(redNorm, greenNorm, blueNorm);
		const min = Math.min(redNorm, greenNorm, blueNorm);
		const delta = max - min;

		let hue = 0;
		if (delta !== 0) {
			if (max === redNorm) {
				hue = 60 * (((greenNorm - blueNorm) / delta) % 6);
			} else if (max === greenNorm) {
				hue = 60 * (((blueNorm - redNorm) / delta) + 2);
			} else {
				hue = 60 * (((redNorm - greenNorm) / delta) + 4);
			}
		}

		if (hue < 0) {
			hue += 360;
		}

		const saturation = max === 0 ? 0 : delta / max;
		return { h: hue, s: saturation, v: max };
	};

	const getWledElements = (device) => {
		if (device === 'shed') {
			return {
				name: 'Shed',
				statusText: wledShedStatusText,
				detailText: wledShedDetailText,
				powerButton: wledShedPowerButton,
				wheel: wledShedWheel,
				wheelWrap: wledShedWheelWrap,
				wheelThumb: wledShedWheelThumb,
			};
		}

		if (device === 'bookcase') {
			return {
				name: 'Bookcase',
				statusText: wledBookcaseStatusText,
				detailText: wledBookcaseDetailText,
				powerButton: wledBookcasePowerButton,
				wheel: wledBookcaseWheel,
				wheelWrap: wledBookcaseWheelWrap,
				wheelThumb: wledBookcaseWheelThumb,
			};
		}

		return null;
	};

	const drawWledWheel = (canvas) => {
		if (!canvas) {
			return;
		}
		const context = canvas.getContext('2d');
		if (!context) {
			return;
		}

		const size = Math.min(canvas.width, canvas.height);
		const image = context.createImageData(size, size);
		const radius = size / 2;
		const pixels = image.data;

		for (let y = 0; y < size; y += 1) {
			for (let x = 0; x < size; x += 1) {
				const dx = x + 0.5 - radius;
				const dy = y + 0.5 - radius;
				const distance = Math.sqrt(dx * dx + dy * dy);
				const offset = (y * size + x) * 4;

				if (distance > radius) {
					pixels[offset + 3] = 0;
					continue;
				}

				const hue = ((Math.atan2(dy, dx) * 180) / Math.PI + 360) % 360;
				const saturation = clamp(distance / radius, 0, 1);
				const rgb = hsvToRgb(hue, saturation, 1);

				pixels[offset] = rgb.r;
				pixels[offset + 1] = rgb.g;
				pixels[offset + 2] = rgb.b;
				pixels[offset + 3] = 255;
			}
		}

		context.clearRect(0, 0, canvas.width, canvas.height);
		context.putImageData(image, 0, 0);
	};

	const setWledWheelThumb = (device, color) => {
		const elements = getWledElements(device);
		if (!elements || !elements.wheel || !elements.wheelThumb) {
			return;
		}

		const rgb = hexToRgb(color);
		if (!rgb) {
			return;
		}

		const hsv = rgbToHsv(rgb.r, rgb.g, rgb.b);
		const radius = elements.wheel.width / 2;
		const distance = clamp(hsv.s, 0, 1) * radius;
		const radians = (hsv.h * Math.PI) / 180;
		const x = radius + Math.cos(radians) * distance;
		const y = radius + Math.sin(radians) * distance;

		elements.wheelThumb.style.left = `${x}px`;
		elements.wheelThumb.style.top = `${y}px`;
		elements.wheelThumb.style.backgroundColor = color;
	};

	const updateWledUI = (device, state) => {
		const elements = getWledElements(device);
		if (!elements) {
			return;
		}

		const isOn = Boolean(state && state.on);
		const color = typeof state?.color === 'string' ? state.color : '#ffffff';
		wledCurrentColor[device] = color;
		if (elements.powerButton) {
			elements.powerButton.dataset.state = isOn ? 'on' : 'off';
			elements.powerButton.setAttribute('aria-pressed', isOn ? 'true' : 'false');
			elements.powerButton.textContent = isOn ? 'Power On' : 'Power Off';
		}

		if (/^#[0-9a-fA-F]{6}$/.test(color)) {
			setWledWheelThumb(device, color);
		}

		const summary = `${elements.name} ${isOn ? 'On' : 'Off'} • ${color.toUpperCase()}`;
		if (elements.statusText) {
			elements.statusText.textContent = summary;
		}
		if (elements.detailText) {
			elements.detailText.textContent = `Color ${color.toUpperCase()}`;
		}
	};

	const setWledUnavailable = (device, message) => {
		const elements = getWledElements(device);
		if (!elements) {
			return;
		}
		if (elements.statusText) {
			elements.statusText.textContent = message;
		}
		if (elements.detailText) {
			elements.detailText.textContent = 'Controller unreachable';
		}
	};

	const syncWledState = async (device) => {
		if (!window.DashboardAPI || typeof window.DashboardAPI.getWledState !== 'function') {
			return;
		}

		try {
			const state = await window.DashboardAPI.getWledState(device);
			updateWledUI(device, state);
		} catch (error) {
			console.error(`Failed to retrieve WLED status for ${device}`, error);
			setWledUnavailable(device, 'Status unavailable');
		}
	};

	const setWledBusy = (device, busy) => {
		const elements = getWledElements(device);
		if (!elements) {
			return;
		}
		wledBusy[device] = busy;
		if (elements.powerButton) {
			elements.powerButton.disabled = busy;
		}
		if (elements.wheelWrap) {
			elements.wheelWrap.dataset.disabled = busy ? 'true' : 'false';
		}
	};

	const setWledPower = async (device, on) => {
		if (wledBusy[device]) {
			return;
		}
		if (!window.DashboardAPI || typeof window.DashboardAPI.setWledPower !== 'function') {
			return;
		}

		setWledBusy(device, true);
		try {
			const state = await window.DashboardAPI.setWledPower(device, on);
			updateWledUI(device, state);
		} catch (error) {
			console.error(`Failed to set WLED power for ${device}`, error);
			setWledUnavailable(device, 'Power update failed');
		} finally {
			setWledBusy(device, false);
		}
	};

	const setWledColor = async (device, color) => {
		if (wledBusy[device]) {
			return;
		}
		if (!window.DashboardAPI || typeof window.DashboardAPI.setWledColor !== 'function') {
			return;
		}

		setWledBusy(device, true);
		try {
			const state = await window.DashboardAPI.setWledColor(device, color);
			updateWledUI(device, state);
		} catch (error) {
			console.error(`Failed to set WLED color for ${device}`, error);
			setWledUnavailable(device, 'Color update failed');
		} finally {
			setWledBusy(device, false);
		}
	};

	const queueWledColor = (device, color) => {
		if (wledColorTimers[device]) {
			window.clearTimeout(wledColorTimers[device]);
		}
		wledCurrentColor[device] = color;
		wledColorTimers[device] = window.setTimeout(() => {
			setWledColor(device, color);
		}, 180);
	};

	const colorFromWheelPointer = (device, clientX, clientY) => {
		const elements = getWledElements(device);
		if (!elements || !elements.wheel) {
			return null;
		}

		const rect = elements.wheel.getBoundingClientRect();
		const centerX = rect.left + rect.width / 2;
		const centerY = rect.top + rect.height / 2;
		const dx = clientX - centerX;
		const dy = clientY - centerY;
		const radius = rect.width / 2;
		const distance = Math.min(Math.sqrt(dx * dx + dy * dy), radius);
		const saturation = clamp(distance / radius, 0, 1);
		const hue = ((Math.atan2(dy, dx) * 180) / Math.PI + 360) % 360;
		const rgb = hsvToRgb(hue, saturation, 1);
		return rgbToHex(rgb.r, rgb.g, rgb.b);
	};

	const bindWledWheel = (device) => {
		const elements = getWledElements(device);
		if (!elements || !elements.wheelWrap) {
			return;
		}

		let dragging = false;
		const updateFromPointer = (event) => {
			const color = colorFromWheelPointer(device, event.clientX, event.clientY);
			if (!color) {
				return;
			}

			const isOn = elements.powerButton && elements.powerButton.dataset.state === 'on';
			updateWledUI(device, { on: isOn, color });
			queueWledColor(device, color);
		};

		elements.wheelWrap.addEventListener('pointerdown', (event) => {
			if (wledBusy[device]) {
				return;
			}
			dragging = true;
			elements.wheelWrap.setPointerCapture(event.pointerId);
			updateFromPointer(event);
		});

		elements.wheelWrap.addEventListener('pointermove', (event) => {
			if (!dragging) {
				return;
			}
			updateFromPointer(event);
		});

		const stopDragging = (event) => {
			if (!dragging) {
				return;
			}
			dragging = false;
			if (elements.wheelWrap.hasPointerCapture(event.pointerId)) {
				elements.wheelWrap.releasePointerCapture(event.pointerId);
			}
		};

		elements.wheelWrap.addEventListener('pointerup', stopDragging);
		elements.wheelWrap.addEventListener('pointercancel', stopDragging);
	};

	const syncAllWledStates = async () => {
		if (wledRefreshButton) {
			wledRefreshButton.disabled = true;
		}
		if (wledShedStatusText) {
			wledShedStatusText.textContent = 'Shed status syncing…';
		}
		if (wledBookcaseStatusText) {
			wledBookcaseStatusText.textContent = 'Bookcase status syncing…';
		}
		await Promise.allSettled([syncWledState('shed'), syncWledState('bookcase')]);
		if (wledRefreshButton) {
			wledRefreshButton.disabled = false;
		}
	};

	const loadMenuData = async () => {
		const imgele = document.getElementById('detection');
		try {
			const controller = new AbortController();
			const payload = await window.DashboardAPI.fetchCassiniData(controller.signal);
			const detection = payload && typeof payload === 'object' && 'detection' in payload ? payload.detection : payload;
			imgele.dataset.timestamp = detection.timestamp;
			const imageCandidates = detection && typeof detection === 'object'
				? [detection.publicUrl, detection.url, detection.imageUrl, detection.path]
				: [];
			let normalizedUrl = null;
			for (const candidate of imageCandidates) {
				normalizedUrl = normalizeCassiniImageUrl(candidate);
				if (normalizedUrl) {
					break;
				}
			}

			const timestampRaw = detection ? extractCassiniTimestamp(detection) : null;
			const timestamp = Number.isFinite(timestampRaw) ? Math.trunc(timestampRaw) : null;
			currentCassiniDetection = detection
				? {
					data: detection,
					timestamp,
					imageUrl: normalizedUrl,
				}
				: null;

			if (imgele && normalizedUrl) {
				imgele.src = normalizedUrl;
			}
		} catch (error) {
			console.error('Failed to load Cassini data', error);
			currentCassiniDetection = null;
		}
		updateCassiniButtons();
	};

	const updateLightUI = (isOn) => {
		if (!lightToggleButton) {
			return;
		}

		lightToggleButton.dataset.state = isOn ? 'on' : 'off';
		lightToggleButton.setAttribute('aria-pressed', isOn ? 'true' : 'false');
		const label = isOn ? 'Light On' : 'Light Off';
		if (lightLabel) {
			lightLabel.textContent = label;
		}

		if (lightStatusText) {
			lightStatusText.textContent = isOn ? 'Light is currently on' : 'Light is currently off';
		}
	};

	const updatePumpUI = (isOn) => {
		if (!pumpToggleButton) {
			return;
		}

		pumpToggleButton.dataset.state = isOn ? 'on' : 'off';
		pumpToggleButton.setAttribute('aria-pressed', isOn ? 'true' : 'false');
		const label = isOn ? 'Pump On' : 'Pump Off';
		if (pumpLabel) {
			pumpLabel.textContent = label;
		}

		if (pumpStatusText) {
			pumpStatusText.textContent = isOn ? 'Pump is currently on' : 'Pump is currently off';
		}
	};

	const syncLightState = async () => {
		if (!lightToggleButton || lightBusy) {
			return;
		}

		try {
			const status = await window.DashboardAPI.getLightState();
			if (status && typeof status.on === 'boolean') {
				updateLightUI(status.on);
			}
		} catch (error) {
			console.error('Failed to retrieve light status', error);
			if (lightStatusText) {
				lightStatusText.textContent = 'Unable to reach light controller';
			}
		}
	};

	const syncPumpState = async () => {
		if (!pumpToggleButton || pumpBusy) {
			return;
		}

		try {
			const status = await window.DashboardAPI.getPumpState();
			if (status && typeof status.on === 'boolean') {
				updatePumpUI(status.on);
			}
		} catch (error) {
			console.error('Failed to retrieve pump status', error);
			if (pumpStatusText) {
				pumpStatusText.textContent = 'Unable to reach pump controller';
			}
		}
	};

	const submitCassiniDecision = async (hasCassini, timestamp) => {
		if (timestamp == null) {
			timestamp = currentCassiniDetection.timestamp;
		}
		console.log('Submitting Cassini decision:', hasCassini, timestamp);
		try {
			await window.DashboardAPI.submitCassiniDecision({
				timestamp: timestamp,
				hasCassini,
			});
			await loadMenuData();
		} catch (error) {
			console.error('Failed to submit Cassini decision', error);
		} finally {
			cassiniSubmitting = false;
			updateCassiniButtons();
		}
		if (!hasCassini) {
			// refresh the page to show new detection
			// window.location.reload();
		}
	};

	const getShedLabel = (record) => {
		if (!record) {
			return '—';
		}
		const label =
			record.label ??
			record.class_name ??
			record.class ??
			record.tag ??
			record.category ??
			record.type ??
			'—';
		return String(label || '—');
	};

	const getShedTimestampDisplay = (record) => {
		if (!record) {
			return '—';
		}
		const timestampRaw =
			record.timestamp ??
			record.captured_at ??
			record.created_at ??
			record.time ??
			record.ts;
		if (timestampRaw == null) {
			return '—';
		}

		let date;
		if (timestampRaw instanceof Date) {
			date = timestampRaw;
		} else if (typeof timestampRaw === 'number') {
			const numeric = timestampRaw > 1e12 ? timestampRaw : timestampRaw * 1000;
			date = new Date(numeric);
		} else {
			const parsed = Number(timestampRaw);
			if (Number.isFinite(parsed)) {
				const numeric = parsed > 1e12 ? parsed : parsed * 1000;
				date = new Date(numeric);
			} else {
				date = new Date(String(timestampRaw));
			}
		}

		if (Number.isNaN(date.getTime())) {
			return '—';
		}

		return shedTimeFormatter.format(date);
	};

	const getHoleConfidenceDisplay = (record) => {
		if (!record) {
			return '—';
		}
		const confidenceRaw =
			record.confidence ??
			record.score ??
			record.prob ??
			record.probability ??
			record.conf;
		if (confidenceRaw == null || confidenceRaw === '') {
			return '—';
		}
		const value = typeof confidenceRaw === 'number' ? confidenceRaw : Number(confidenceRaw);
		if (!Number.isFinite(value)) {
			return '—';
		}
		const percent = value > 1 ? value : value * 100;
		return `${percent.toFixed(1)}%`;
	}

	const getHoleTimestampDisplay = (record) => {
		if (!record) {
			return '—';
		}
		const timestampRaw =
			record.timestamp ??
			record.captured_at ??
			record.created_at ??
			record.time ??
			record.ts;
		if (timestampRaw == null) {
			return '—';
		}

		let date;
		if (timestampRaw instanceof Date) {
			date = timestampRaw;
		} else if (typeof timestampRaw === 'number') {
			const numeric = timestampRaw > 1e12 ? timestampRaw : timestampRaw * 1000;
			date = new Date(numeric);
		} else {
			const parsed = Number(timestampRaw);
			if (Number.isFinite(parsed)) {
				const numeric = parsed > 1e12 ? parsed : parsed * 1000;
				date = new Date(numeric);
			} else {
				date = new Date(String(timestampRaw));
			}
		}

		if (Number.isNaN(date.getTime())) {
			return '—';
		}

		return shedTimeFormatter.format(date);
	}


	const getShedConfidenceDisplay = (record) => {
		if (!record) {
			return '—';
		}
		const confidenceRaw =
			record.confidence ??
			record.score ??
			record.prob ??
			record.probability ??
			record.conf;
		if (confidenceRaw == null || confidenceRaw === '') {
			return '—';
		}
		const value = typeof confidenceRaw === 'number' ? confidenceRaw : Number(confidenceRaw);
		if (!Number.isFinite(value)) {
			return '—';
		}
		const percent = value > 1 ? value : value * 100;
		return `${percent.toFixed(1)}%`;
	};

	const toShedImageUrl = (path) => {
		if (!path || typeof path !== 'string') {
			return null;
		}
		if (path.startsWith('http://') || path.startsWith('https://')) {
			return path;
		}
		if (path.startsWith(SHED_IMAGE_PREFIX)) {
			const relative = path.slice(SHED_IMAGE_PREFIX.length);
			return `${SHED_IMAGE_BASE}${relative}`;
		}
		const trimmed = path.replace(/^\/+/, '');
		return `${SHED_IMAGE_BASE}${trimmed}`;
	};

	const updateShedPreview = (record) => {
		if (!shedPreviewImage || !shedPreviewPlaceholder) {
			return;
		}

		if (!record) {
			shedPreviewImage.hidden = true;
			shedPreviewImage.removeAttribute('src');
			shedPreviewPlaceholder.hidden = false;
			shedPreviewPlaceholder.textContent = shedPreviewDefaultText;
			return;
		}

		const imageUrl =
			record.path ??
			record.image ??
			record.image_path ??
			record.snapshot ??
			record.photo;
		const normalizedUrl = toShedImageUrl(imageUrl);

		if (normalizedUrl) {
			shedPreviewImage.src = normalizedUrl;
			shedPreviewImage.hidden = false;
			shedPreviewImage.alt = `Shed camera detection: ${getShedLabel(record)}`;
			shedPreviewPlaceholder.hidden = true;
		} else {
			shedPreviewImage.hidden = true;
			shedPreviewImage.removeAttribute('src');
			shedPreviewPlaceholder.hidden = false;
			shedPreviewPlaceholder.textContent = 'No image available for this detection.';
		}
	};

	const selectShedRow = (row, record) => {
		if (shedSelectedRow && shedSelectedRow !== row) {
			shedSelectedRow.classList.remove('shed-camera__row--selected');
			shedSelectedRow.setAttribute('aria-pressed', 'false');
		}

		shedSelectedRow = row;

		if (shedSelectedRow) {
			shedSelectedRow.classList.add('shed-camera__row--selected');
			shedSelectedRow.setAttribute('aria-pressed', 'true');
		}

		updateShedPreview(record);

		if (shedStatusText) {
			if (record) {
				const label = getShedLabel(record);
				const captured = getShedTimestampDisplay(record);
				const confidence = getShedConfidenceDisplay(record);
				const parts = [`Viewing ${label}`];
				const displayIndex = row && typeof row.dataset.recordIndex !== 'undefined' ? Number(row.dataset.recordIndex) : NaN;
				if (captured !== '—') {
					parts.push(captured);
				}
				if (confidence !== '—') {
					parts.push(confidence);
				}
				if (!Number.isNaN(displayIndex) && shedMatchCount) {
					parts.push(`Detection ${displayIndex + 1} of ${shedMatchCount}`);
				}
				shedStatusText.textContent = parts.join(' • ');
			} else {
				shedStatusText.textContent = shedStatusDefaultText;
			}
		}
	};
	const setPressedState = (activeButton) => {
		actionButtons.forEach((button) => {
			const isActive = button === activeButton;
			button.setAttribute('aria-pressed', isActive ? 'true' : 'false');
			button.toggleAttribute('data-active', isActive);
		});
	};

	const closeMenu = () => {
		dashboard.classList.remove('menu-open');
		dashboard.dataset.activeMenu = '';
		dashboard.dataset.state = 'image';
		setPressedState(null);
	};

	const openMenu = (targetButton) => {
		const menuKey = targetButton.dataset.menu;
		const alreadyOpen = dashboard.dataset.activeMenu === menuKey && dashboard.classList.contains('menu-open');

		if (alreadyOpen) {
			closeMenu();
			return;
		}

		dashboard.dataset.activeMenu = menuKey;
		dashboard.dataset.state = 'menu';
		dashboard.classList.add('menu-open');
		setPressedState(targetButton);

		if (menuKey === 'settings') {
			loadShedCameraData();
		}

		if (menuKey === 'wled') {
			syncAllWledStates();
		}
	};

	loadMenuData();
	syncLightState();
	syncPumpState();
	drawWledWheel(wledShedWheel);
	drawWledWheel(wledBookcaseWheel);
	bindWledWheel('shed');
	bindWledWheel('bookcase');
	syncAllWledStates();
	updateCassiniButtons();

	const renderShedCameraData = (records) => {
		if (!shedTableBody) {
			return 0;
		}

		shedTableBody.innerHTML = '';
		shedDisplayCache = [];
		if (shedSelectedRow) {
			shedSelectedRow.classList.remove('shed-camera__row--selected');
			shedSelectedRow.setAttribute('aria-pressed', 'false');
		}
		shedSelectedRow = null;

		if (!records || records.length === 0) {
			shedMatchCount = 0;
			const emptyRow = document.createElement('tr');
			emptyRow.className = 'shed-camera__empty';
			const cell = document.createElement('td');
			cell.colSpan = 3;
			cell.textContent = 'No detections available.';
			emptyRow.appendChild(cell);
			shedTableBody.appendChild(emptyRow);
			updateShedPreview(null);
			if (shedStatusText) {
				shedStatusText.textContent = 'No detections returned';
			}
			return 0;
		}

		const displayRecords = [];
		for (const record of records) {
			const labelText = getShedLabel(record);
			const normalizedLabel = labelText.toLowerCase();
			if (allowedShedLabels.size && !allowedShedLabels.has(normalizedLabel)) {
				continue;
			}
			displayRecords.push(record);
			if (displayRecords.length >= 12) {
				break;
			}
		}

		if (displayRecords.length === 0) {
			shedMatchCount = 0;
			const emptyRow = document.createElement('tr');
			emptyRow.className = 'shed-camera__empty';
			const cell = document.createElement('td');
			cell.colSpan = 3;
			cell.textContent = 'No tracked detections found.';
			emptyRow.appendChild(cell);
			shedTableBody.appendChild(emptyRow);
			updateShedPreview(null);
			if (shedStatusText) {
				shedStatusText.textContent = 'No tracked detections found';
			}
			return 0;
		}

		displayRecords.forEach((record, index) => {
			const row = document.createElement('tr');
			row.className = 'shed-camera__row';
			row.dataset.recordIndex = String(index);
			row.tabIndex = 0;
			row.setAttribute('role', 'button');
			row.setAttribute('aria-pressed', 'false');

			const labelText = getShedLabel(record);
			const labelNormalized = labelText.toLowerCase();
			if (labelNormalized === 'person') {
				row.classList.add('shed-camera__row--person');
			} else if (labelNormalized === 'cat' || labelNormalized === 'dog') {
				row.classList.add('shed-camera__row--pet');
			} else {
				row.classList.add('shed-camera__row--other');
			}

			const capturedText = getShedTimestampDisplay(record);
			const confidenceText = getShedConfidenceDisplay(record);

			row.setAttribute('aria-label', `${labelText} detected ${capturedText}`);

			const captureCell = document.createElement('td');
			captureCell.textContent = capturedText;

			const labelCell = document.createElement('td');
			labelCell.textContent = labelText;

			const confidenceCell = document.createElement('td');
			confidenceCell.textContent = confidenceText;

			row.append(captureCell, labelCell, confidenceCell);

			const handleSelect = () => selectShedRow(row, record);
			row.addEventListener('click', handleSelect);
			row.addEventListener('keydown', (event) => {
				if (event.key === 'Enter' || event.key === ' ') {
					event.preventDefault();
					handleSelect();
				}
			});

			shedTableBody.appendChild(row);
		});

		shedDisplayCache = displayRecords;
		updateShedPreview(null);

		if (shedStatusText) {
			const totalMatches = records.filter((record) => {
				const labelText = getShedLabel(record).toLowerCase();
				return !allowedShedLabels.size || allowedShedLabels.has(labelText);
			}).length;
			const visibleCount = shedDisplayCache.length;
			shedMatchCount = totalMatches;
			shedStatusText.textContent = `Showing ${visibleCount} of ${totalMatches} detections`;
		}

		const firstRow = shedTableBody.querySelector('.shed-camera__row');
		if (firstRow && shedDisplayCache.length > 0) {
			selectShedRow(firstRow, shedDisplayCache[0]);
		}

		return shedDisplayCache.length;
	};

	const loadShedCameraData = async (options = {}) => {
		if (!window.DashboardAPI || typeof window.DashboardAPI.fetchShedCameraData !== 'function') {
			return;
		}

		if (shedLoading) {
			return;
		}

		if (shedDataCache && !options.force) {
			renderShedCameraData(shedDataCache);
			return;
		}

		shedLoading = true;
		if (shedStatusText) {
			shedStatusText.textContent = 'Loading latest detections…';
		}
		if (shedRefreshButton) {
			shedRefreshButton.disabled = true;
		}

		try {
			const payload = await window.DashboardAPI.fetchShedCameraData();
			const records = Array.isArray(payload)
				? payload
				: Array.isArray(payload?.records)
				? payload.records
				: [];
			shedDataCache = records;
			renderShedCameraData(records);
		} catch (error) {
			console.error('Failed to load Shed Camera data', error);
			shedDataCache = null;
			renderShedCameraData([]);
			if (shedStatusText) {
				shedStatusText.textContent = 'Unable to load shed camera data';
			}
		} finally {
			shedLoading = false;
			if (shedRefreshButton) {
				shedRefreshButton.disabled = false;
			}
		}
	};

	// Prefetch shed camera data in the background
	loadShedCameraData({ force: true });

	const loadHoleCameraData = async (options = {}) => {
		if (!window.DashboardAPI || typeof window.DashboardAPI.fetchHoleCameraData !== 'function') {
			return;
		}

		if (holeLoading) {
			return;
		}

		if (holeDataCache && !options.force) {
			renderHoleCameraData(holeDataCache);
			return;
		}

		holeLoading = true;
		if (holeStatusText) {
			holeStatusText.textContent = 'Loading latest detections…';
		}
		if (holeRefreshButton) {
			holeRefreshButton.disabled = true;
		}

		try {
			const payload = await window.DashboardAPI.fetchHoleCameraData();
			const records = Array.isArray(payload)
				? payload
				: Array.isArray(payload?.records)
				? payload.records
				: [];
			holeDataCache = records;
			renderHoleCameraData(records);
		} catch (error) {
			console.error('Failed to load Hole Camera data', error);
			holeDataCache = null;
			renderHoleCameraData([]);
			if (holeStatusText) {
				holeStatusText.textContent = 'Unable to load hole camera data';
			}
		} finally {
			holeLoading = false;
			if (holeRefreshButton) {
				holeRefreshButton.disabled = false;
			}
		}
	}

	const getHoleImage = (path) => {
		const url = path.replace(HOLE_IMAGE_PREFIX,"");
		return `${HOLE_IMAGE_BASE}/${url}`;
	};

	const selectHoleRow = (row, record) => {
		if (shedSelectedRow && shedSelectedRow !== row) {
			shedSelectedRow.classList.remove('shed-camera__row--selected');
			shedSelectedRow.setAttribute('aria-pressed', 'false');
		}
		console.log('Selecting hole row', row, record);
		shedSelectedRow = row;
		if (shedSelectedRow) {
			shedSelectedRow.classList.add('shed-camera__row--selected');
			shedSelectedRow.setAttribute('aria-pressed', 'true');
		}

		if (!record) {
			return;
		}

		if (holePreviewImage && record) {
			let imageUrl =
				record.path ??
				record.image ??
				record.image_path ??
				record.snapshot ??
				record.photo;
			imageUrl = getHoleImage(imageUrl);
			console.log(imageUrl);
			if (imageUrl) {
				holePreviewImage.src = imageUrl;
				holePreviewImage.hidden = false;
				holePreviewImage.alt = `Hole camera detection`;
				holePreviewPlaceholder.hidden = true;
			} else {
				holePreviewImage.hidden = true;
				holePreviewImage.removeAttribute('src');
				holePreviewPlaceholder.hidden = false;
				holePreviewPlaceholder.textContent = 'No image available for this detection.';
			}
		}
	};

	const renderHoleCameraData = (records) => {
		if (!holePreviewImage || !holePreviewPlaceholder) {
			return;
		}
		const holeTableBody = document.querySelector('[data-hole-rows]');

		if (!records || records.length === 0) {
			currentHoleDetection = null;
			holePreviewImage.hidden = true;
			holePreviewImage.removeAttribute('src');
			holePreviewPlaceholder.hidden = false;
			holePreviewPlaceholder.textContent = 'No detections available.';
			if (holeStatusText) {
				holeStatusText.textContent = 'No detections returned';
			}
			return;
		}
		
		let displayRecords = [];
		for (const record of records) {

			displayRecords.push(record);
			if (displayRecords.length >= 30) {
				break;
			}
		}

		if (displayRecords.length === 0) {
			currentHoleDetection = null;
			holePreviewImage.hidden = true;
			holePreviewImage.removeAttribute('src');
			holePreviewPlaceholder.hidden = false;
			holePreviewPlaceholder.textContent = 'No tracked detections found.';
			if (holeStatusText) {
				holeStatusText.textContent = 'No tracked detections found';
			}
			return;
		}

		
		currentHoleDetection = displayRecords[0];

		const imageUrl = currentHoleDetection.path 

		// const normalizedUrl = toShedImageUrl(imageUrl);
		displayRecords.forEach((record, index) => {
			const row = document.createElement('tr');
			row.className = 'shed-camera__row';
			row.dataset.recordIndex = String(index);
			row.tabIndex = 0;
			row.setAttribute('role', 'button');
			row.setAttribute('aria-pressed', 'false');

			const labelNormalized = "cassini";


			const capturedText = getHoleTimestampDisplay(record);
			const confidenceText = getHoleConfidenceDisplay(record);
			const labelText = record.hasCassini ? 'Cassini Present' : 'No Cassini';

			// row.setAttribute('aria-label', `${labelText} detected ${capturedText}`);

			const captureCell = document.createElement('td');
			captureCell.textContent = capturedText;

			const labelCell = document.createElement('td');
			labelCell.textContent = labelText;

			const confidenceCell = document.createElement('td');
			confidenceCell.textContent = confidenceText;

			const yesCell = document.createElement('td');
			yesCell.textContent = "Yes";

			const noCell = document.createElement('td');
			noCell.textContent = "No";

			if (record.hasTurtle == 1	) {
				yesCell.style.border = '2px solid green';
			}
			else if (record.hasTurtle == 0	) {
				noCell.style.border = '2px solid red';
			}
			else {
				// no border
			}

			row.dataset.timestamp = String(record.timestamp ?? '');
			row.append(captureCell, yesCell, noCell);

			yesCell.addEventListener('click', () => {
				// make invisible call to submitCassiniDecision with true
				yesCell.style.border = '2px solid green';
				const timestamp = parseInt(yesCell.parentElement.dataset.timestamp);
				submitCassiniDecision(true, timestamp);
			});
			noCell.addEventListener('click', () => {
				noCell.style.border = '2px solid red';
				const timestamp = parseInt(noCell.parentElement.dataset.timestamp);
				submitCassiniDecision(false, timestamp);
			});

			const handleSelect = () => selectHoleRow(row, record);
			row.addEventListener('click', handleSelect);
			row.addEventListener('keydown', (event) => {
				if (event.key === 'Enter' || event.key === ' ') {
					event.preventDefault();
					handleSelect();
				}
			});

			holeTableBody.appendChild(row);
		});

		if (imageUrl) {
			holePreviewImage.src = imageUrl;
			holePreviewImage.hidden = false;
			holePreviewImage.alt = `Hole camera detection`;
			holePreviewPlaceholder.hidden = true;
		} else {
			holePreviewImage.hidden = true;
			holePreviewImage.removeAttribute('src');
			holePreviewPlaceholder.hidden = false;
			holePreviewPlaceholder.textContent = 'No image available for this detection.';
		}

		if (holeStatusText) {
			const totalMatches = records.length;
			holeStatusText.textContent = `Showing latest detection of ${totalMatches}`;
		}
		
	};

	if (shedRefreshButton) {
		shedRefreshButton.addEventListener('click', () => {
			loadShedCameraData({ force: true });
		});
	}

	if (lightToggleButton) {
		lightToggleButton.addEventListener('click', async () => {
			if (lightBusy) {
				return;
			}

			lightBusy = true;
			lightToggleButton.disabled = true;
			try {
				const response = await window.DashboardAPI.toggleLight();
				if (response && typeof response.on === 'boolean') {
					updateLightUI(response.on);
				}
			} catch (error) {
				console.error('Failed to toggle light', error);
				if (lightStatusText) {
					lightStatusText.textContent = 'Toggle failed – check connection';
				}
			} finally {
				lightBusy = false;
				lightToggleButton.disabled = false;
			}
		});
	}


	loadHoleCameraData();

	if (pumpToggleButton) {
		pumpToggleButton.addEventListener('click', async () => {
			if (pumpBusy) {
				return;
			}

			pumpBusy = true;
			pumpToggleButton.disabled = true;
			try {
				const response = await window.DashboardAPI.togglePump();
				if (response && typeof response.on === 'boolean') {
					updatePumpUI(response.on);
				}
			} catch (error) {
				console.error('Failed to toggle pump', error);
				if (pumpStatusText) {
					pumpStatusText.textContent = 'Pump toggle failed – check connection';
				}
			} finally {
				pumpBusy = false;
				pumpToggleButton.disabled = false;
			}
		});
	}

	if (cassiniYesButton) {
		cassiniYesButton.addEventListener('click', () => {
			submitCassiniDecision(true);
		});
	}

	if (cassiniNoButton) {
		cassiniNoButton.addEventListener('click', () => {
			submitCassiniDecision(false);
		});
	}

	if (wledShedPowerButton) {
		wledShedPowerButton.addEventListener('click', () => {
			const shouldTurnOn = wledShedPowerButton.dataset.state !== 'on';
			setWledPower('shed', shouldTurnOn);
		});
	}

	if (wledBookcasePowerButton) {
		wledBookcasePowerButton.addEventListener('click', () => {
			const shouldTurnOn = wledBookcasePowerButton.dataset.state !== 'on';
			setWledPower('bookcase', shouldTurnOn);
		});
	}

	if (wledRefreshButton) {
		wledRefreshButton.addEventListener('click', () => {
			syncAllWledStates();
		});
	}

	actionButtons.forEach((button) => {
		button.addEventListener('click', () => openMenu(button));
		button.addEventListener('keydown', (event) => {
			if (event.key === 'Enter' || event.key === ' ') {
				event.preventDefault();
				openMenu(button);
			}
		});
	});

	closeButtons.forEach((button) => {
		button.addEventListener('click', closeMenu);
	});

	document.addEventListener('keydown', (event) => {
		if (event.key === 'Escape') {
			closeMenu();
		}
	});
});
