<script lang="ts">
	// TPAI Gmail connect finalization (external-content m3, S16).
	// The connector's OAuth callback redirects here with a one-time
	// gmail_confirm nonce. Confirming from THIS authenticated session is the
	// account-linking defense: the connector activates the pending
	// connection only if the signed-in user is the identity that initiated
	// the consent — anything else destroys it. The nonce is consumed
	// immediately on load, success or not.
	import { onMount, getContext } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { base } from '$app/paths';

	import { confirmGmail } from '$lib/apis/connectors';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	let state: 'working' | 'connected' | 'mismatch' | 'invalid' | 'error' = 'working';
	let accountLabel: string | null = null;

	onMount(async () => {
		const nonce = $page.url.searchParams.get('gmail_confirm') ?? '';
		// Strip the nonce from the address bar/history immediately (base-aware
		// so a subpath deploy rewrites to a route that actually exists).
		window.history.replaceState({}, '', `${base}/connectors/gmail/callback`);
		if (!nonce) {
			state = 'invalid';
			return;
		}
		try {
			const res = await confirmGmail(localStorage.token, nonce);
			if (res?.confirmed) {
				accountLabel = res.account_label ?? null;
				state = 'connected';
				return;
			}
			state = 'error';
		} catch (error) {
			const reason = typeof error === 'object' && error !== null ? error['reason'] : error;
			if (reason === 'confirm-mismatch') {
				state = 'mismatch';
			} else if (reason === 'confirm-invalid') {
				state = 'invalid';
			} else {
				state = 'error';
			}
		}
	});
</script>

<div class="w-full h-screen flex items-center justify-center">
	<div class="max-w-md w-full mx-4 text-sm">
		{#if state === 'working'}
			<div class="flex items-center gap-2">
				<Spinner className="size-4" />
				<div>{$i18n.t('Finishing your Gmail connection…')}</div>
			</div>
		{:else if state === 'connected'}
			<div class="font-medium text-green-600 dark:text-green-400">
				{$i18n.t('Gmail connected')}{accountLabel ? ` · ${accountLabel}` : ''}
			</div>
			<div class="mt-1 text-gray-500">
				{$i18n.t('You can manage this connection in Settings → Connectors.')}
			</div>
		{:else if state === 'mismatch'}
			<div class="font-medium text-red-600 dark:text-red-400">
				{$i18n.t('This connection could not be completed')}
			</div>
			<div class="mt-1 text-gray-500">
				{$i18n.t(
					'The Google consent was started by a different account, so it was canceled and nothing was connected. Start again from Settings → Connectors.'
				)}
			</div>
		{:else if state === 'invalid'}
			<div class="font-medium text-red-600 dark:text-red-400">
				{$i18n.t('This connection link is no longer valid')}
			</div>
			<div class="mt-1 text-gray-500">
				{$i18n.t(
					'Connection links are single-use and expire after a few minutes. Start again from Settings → Connectors.'
				)}
			</div>
		{:else}
			<div class="font-medium text-red-600 dark:text-red-400">
				{$i18n.t('Something went wrong finishing the connection')}
			</div>
			<div class="mt-1 text-gray-500">
				{$i18n.t('Check Settings → Connectors for the current status, or try again.')}
			</div>
		{/if}

		<div class="mt-4">
			<button
				class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
				on:click={() => goto('/')}
			>
				{$i18n.t('Back to TPAI')}
			</button>
		</div>
	</div>
</div>
