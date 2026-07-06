<script lang="ts">
	// TPAI Connectors page (Settings -> Connectors, external-content m3, R14).
	// Manages the user's Gmail connection through the OWUI backend -> TPAI
	// gateway proxy. The consent flow MUST originate here, in the user's own
	// authenticated browser (account-linking defense): "Connect" creates the
	// consent session server-side and navigates THIS tab to Google; after
	// consent, the OAuth callback redirects back to /connectors/gmail/callback,
	// which confirms the connection under this user's session.
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';

	import { connectGmail, disconnectGmail, getGmailStatus } from '$lib/apis/connectors';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	let loading = true;
	let busy = false;
	let status: null | {
		status: string;
		account_label?: string | null;
		connected_at?: string | null;
		broken_reason?: string | null;
	} = null;

	const refresh = async () => {
		loading = true;
		try {
			status = await getGmailStatus(localStorage.token);
		} catch (error) {
			toast.error(`${error}`);
			status = null;
		}
		loading = false;
	};

	const connectHandler = async () => {
		busy = true;
		try {
			const res = await connectGmail(localStorage.token);
			if (res?.consent_url) {
				// Same-tab navigation: the consent link is single-use, bound to
				// this user, and never displayed or copyable.
				window.location.href = res.consent_url;
				return;
			}
			toast.error($i18n.t('Could not start the Gmail connection.'));
		} catch (error) {
			toast.error(`${error}`);
		}
		busy = false;
	};

	const disconnectHandler = async () => {
		busy = true;
		try {
			const res = await disconnectGmail(localStorage.token);
			if (res?.disconnected) {
				toast.success($i18n.t('Gmail disconnected.'));
			}
		} catch (error) {
			toast.error(`${error}`);
		}
		await refresh();
		busy = false;
	};

	onMount(refresh);
</script>

<div class="flex flex-col h-full justify-between text-sm">
	<div class="overflow-y-scroll scrollbar-hidden h-full">
		<div class="mb-3.5">
			<div class="mb-2.5 text-base font-medium">{$i18n.t('Connectors')}</div>

			<hr class="border-gray-100 dark:border-gray-850 my-2" />

			<div class="my-2">
				<div class="flex items-center justify-between">
					<div>
						<div class="font-medium">{$i18n.t('Gmail')}</div>
						<div class="text-xs text-gray-500 mt-0.5">
							{$i18n.t(
								'Read-only access to your own inbox, so your assistant can answer questions about your email. You can disconnect at any time.'
							)}
						</div>
					</div>
				</div>

				<div class="mt-3">
					{#if loading}
						<Spinner className="size-4" />
					{:else if status === null}
						<div class="text-xs text-gray-500">
							{$i18n.t('Connector status is unavailable right now.')}
						</div>
					{:else if status.status === 'connected'}
						<div class="flex items-center justify-between">
							<div class="text-xs">
								<span class="text-green-600 dark:text-green-400 font-medium">
									{$i18n.t('Connected')}
								</span>
								{#if status.account_label}
									<span class="text-gray-500"> · {status.account_label}</span>
								{/if}
							</div>
							<button
								class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-50"
								disabled={busy}
								on:click={disconnectHandler}
							>
								{$i18n.t('Disconnect')}
							</button>
						</div>
					{:else if status.status === 'broken'}
						<div class="flex items-center justify-between">
							<div class="text-xs text-yellow-600 dark:text-yellow-400">
								{$i18n.t('Reconnection needed — Google access was revoked or expired.')}
							</div>
							<div class="flex gap-2">
								<button
									class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-50"
									disabled={busy}
									on:click={disconnectHandler}
								>
									{$i18n.t('Disconnect')}
								</button>
								<button
									class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-50"
									disabled={busy}
									on:click={connectHandler}
								>
									{$i18n.t('Reconnect')}
								</button>
							</div>
						</div>
					{:else if status.status === 'pending-confirm'}
						<div class="flex items-center justify-between">
							<div class="text-xs text-gray-500">
								{$i18n.t(
									'A connection is waiting to be finished. Complete the Google consent flow, or start over.'
								)}
							</div>
							<button
								class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-50"
								disabled={busy}
								on:click={connectHandler}
							>
								{$i18n.t('Start over')}
							</button>
						</div>
					{:else}
						<div class="flex items-center justify-between">
							<div class="text-xs text-gray-500">{$i18n.t('Not connected')}</div>
							<button
								class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full disabled:opacity-50"
								disabled={busy}
								on:click={connectHandler}
							>
								{$i18n.t('Connect')}
							</button>
						</div>
					{/if}
				</div>

				<div class="mt-3">
					<button
						class="text-xs text-gray-500 underline"
						disabled={loading || busy}
						on:click={refresh}
					>
						{$i18n.t('Refresh status')}
					</button>
				</div>
			</div>
		</div>
	</div>
</div>
