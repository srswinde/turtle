'use strict';

(() => {
	const cassiniEndpoint = '/app/api/cassini';
	const cassiniDesisionEndpoint = '/app/api/cassini/decision';
	const lightEndpoint = '/app/api/light';
	const pumpEndpoint = '/app/api/pump';
	const shedCameraEndpoint = '/app/api/shed-camera';
	const holeCameraEndpoint = '/app/api/hole-camera';
	const wledEndpoint = '/app/api/wled';
	const jsonHeaders = { Accept: 'application/json' };

	const requestJson = async (url, options = {}) => {
		const response = await fetch(url, {
			...options,
			headers: { ...jsonHeaders, ...options.headers },
		});

		if (!response.ok) {
			throw new Error(`${options.method || 'GET'} ${url} failed with ${response.status}`);
		}

		return response.json();
	};

	const fetchCassiniData = (signal) =>
		requestJson(cassiniEndpoint, {
			method: 'POST',
			signal,
		});

	const getLightState = (signal) =>
		requestJson(lightEndpoint, {
			signal,
		});

	const toggleLight = (signal) =>
		requestJson(`${lightEndpoint}/toggle`, {
			method: 'POST',
			signal,
			headers: { 'Content-Type': 'application/json' },
			body: '{}',
		});

	const getPumpState = (signal) =>
		requestJson(pumpEndpoint, {
			signal,
		});

	const togglePump = (signal) =>
		requestJson(`${pumpEndpoint}/toggle`, {
			method: 'POST',
			signal,
			headers: { 'Content-Type': 'application/json' },
			body: '{}',
		});

	const fetchShedCameraData = (signal) =>
		requestJson(shedCameraEndpoint, {
			signal,
		});

	const fetchHoleCameraData = (signal) =>
		requestJson(holeCameraEndpoint, {
			signal,
		});

	const submitCassiniDecision = (decision, signal) =>
		requestJson(`${cassiniDesisionEndpoint}`, {
			method: 'POST',
			signal,
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ decision }),
		});

	const getWledState = (device, signal) =>
		requestJson(`${wledEndpoint}/${encodeURIComponent(device)}`, {
			signal,
		});

	const setWledPower = (device, on, signal) =>
		requestJson(`${wledEndpoint}/${encodeURIComponent(device)}/power`, {
			method: 'POST',
			signal,
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ on: Boolean(on) }),
		});

	const setWledColor = (device, color, signal) =>
		requestJson(`${wledEndpoint}/${encodeURIComponent(device)}/color`, {
			method: 'POST',
			signal,
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ color }),
		});

	window.DashboardAPI = {
		fetchCassiniData,
		getLightState,
		toggleLight,
		getPumpState,
		togglePump,
		fetchShedCameraData,
		fetchHoleCameraData,
		submitCassiniDecision,
		getWledState,
		setWledPower,
		setWledColor,
	};
})();
