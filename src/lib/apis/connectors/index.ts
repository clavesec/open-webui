// TPAI Connectors API client (Settings -> Connectors, external-content m3).
// All calls go to the OWUI backend, which proxies them to the TPAI gateway;
// the browser never talks to the connector or the gateway directly.
import { WEBUI_API_BASE_URL } from '$lib/constants';

const CONNECTORS_API_BASE_URL = `${WEBUI_API_BASE_URL}/connectors`;

const request = async (token: string, method: string, path: string, body?: object) => {
	let error = null;

	const res = await fetch(`${CONNECTORS_API_BASE_URL}${path}`, {
		method,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		...(body && { body: JSON.stringify(body) })
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err.detail ?? 'Server connection failed';
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getGmailStatus = async (token: string) => request(token, 'GET', '/gmail/status');

export const connectGmail = async (token: string) => request(token, 'POST', '/gmail/connect');

export const confirmGmail = async (token: string, nonce: string) =>
	request(token, 'POST', '/gmail/confirm', { nonce });

export const disconnectGmail = async (token: string) => request(token, 'POST', '/gmail/disconnect');
