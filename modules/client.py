import asyncio
import random

from asyncio import sleep
from aiohttp import ClientSession, TCPConnector
from aiohttp_socks import ProxyConnector
from eth_typing import HexStr
from web3.contract import AsyncContract
from web3.exceptions import TransactionNotFound
from modules.interfaces import PriceImpactException, BlockchainException, SoftwareException
from modules import Logger
from utils.networks import Network
from config import ERC20_ABI, TOKENS_PER_CHAIN, ETH_PRICE, CHAIN_IDS, TOKENS_PER_CHAIN2
from web3 import AsyncHTTPProvider, AsyncWeb3
from config import RHINO_CHAIN_INFO, ORBITER_CHAINS_INFO, LAYERSWAP_CHAIN_NAME
from general_settings import (
    GAS_LIMIT_MULTIPLIER,
    UNLIMITED_APPROVE,
    AMOUNT_PERCENT,
    MIN_BALANCE,
    LIQUIDITY_AMOUNT,
    PRICE_IMPACT,
    GLOBAL_NETWORK, GAS_PRICE_MULTIPLIER,
)
from settings import (
    ORBITER_CHAIN_ID_TO,
    ORBITER_BRIDGE_AMOUNT,
    LAYERSWAP_CHAIN_ID_TO,
    LAYERSWAP_BRIDGE_AMOUNT,
    RHINO_CHAIN_ID_TO,
    RHINO_BRIDGE_AMOUNT,
    ACROSS_CHAIN_ID_TO,
    ACROSS_BRIDGE_AMOUNT, WAIT_FOR_RECEIPT, RELAY_CHAIN_ID_TO, RELAY_BRIDGE_AMOUNT, OWLTO_CHAIN_ID_TO,
    OWLTO_BRIDGE_AMOUNT, BUNGEE_CHAIN_ID_TO, BUNGEE_BRIDGE_AMOUNT, NITRO_CHAIN_ID_TO, NITRO_BRIDGE_AMOUNT,
    ACROSS_TOKEN_NAME, BUNGEE_TOKEN_NAME, LAYERSWAP_TOKEN_NAME, NITRO_TOKEN_NAME, ORBITER_TOKEN_NAME, OWLTO_TOKEN_NAME,
    RELAY_TOKEN_NAME, RHINO_TOKEN_NAME
)


class Client(Logger):
    def __init__(self, account_name: str | int, private_key: str, network: Network, proxy: None | str = None):
        Logger.__init__(self)
        self.network = network
        self.eip1559_support = network.eip1559_support
        self.token = network.token
        self.explorer = network.explorer
        self.chain_id = network.chain_id

        self.proxy_init = proxy
        self.session = ClientSession(connector=ProxyConnector.from_url(f"http://{proxy}", verify_ssl=False)
                                     if proxy else TCPConnector(verify_ssl=False))
        self.request_kwargs = {"proxy": f"http://{proxy}"} if proxy else {}
        self.rpc = random.choice(network.rpc)
        self.w3 = AsyncWeb3(AsyncHTTPProvider(self.rpc, request_kwargs=self.request_kwargs))
        self.account_name = str(account_name)
        self.private_key = private_key
        self.address = AsyncWeb3.to_checksum_address(self.w3.eth.account.from_key(private_key).address)
        self.acc_info = account_name, self.address

    @staticmethod
    def round_amount(min_amount: float, max_amount: float) -> float:
        if not isinstance(min_amount, float | int) or not isinstance(max_amount, float | int):
            raise SoftwareException('This setting does not support % amounts')
        decimals = max(len(str(min_amount)) - 1, len(str(max_amount)) - 1) + 1
        max_decimals = 6
        return round(random.uniform(min_amount, max_amount), decimals if decimals <= max_decimals else 6)

    @staticmethod
    def get_normalize_error(error: Exception) -> Exception | str:
        try:
            if isinstance(error.args[0], dict):
                error = error.args[0].get('message', error)
            return error
        except:
            return error

    async def change_rpc(self):
        self.logger_msg(
            self.account_name, None, msg=f'Trying to replace RPC', type_msg='warning')

        if len(self.network.rpc) != 1:
            rpcs_list = [rpc for rpc in self.network.rpc if rpc != self.rpc]
            new_rpc = random.choice(rpcs_list)
            self.w3 = AsyncWeb3(AsyncHTTPProvider(new_rpc, request_kwargs=self.request_kwargs))
            self.logger_msg(
                self.account_name, None,
                msg=f'RPC successfully replaced. New RPC: {new_rpc}', type_msg='success')
        else:
            self.logger_msg(
                self.account_name, None,
                msg=f'This network has only 1 RPC, no replacement is possible', type_msg='warning')

    def to_wei(self, number: int | float | str, decimals: int = 18) -> int:

        unit_name = {
            18: 'ether',
            6: 'mwei'
        }[decimals]

        return self.w3.to_wei(number=number, unit=unit_name)

    async def get_decimals(self, token_name: str = None, token_address: str = None, omnicheck:bool = False) -> int:
        if omnicheck:
            contract_address = token_address if token_address else TOKENS_PER_CHAIN2[self.network.name][token_name]
        else:
            contract_address = token_address if token_address else TOKENS_PER_CHAIN[self.network.name][token_name]
        contract = self.get_contract(contract_address)
        return await contract.functions.decimals().call()

    async def get_normalize_amount(self, token_name: str, amount_in_wei: int) -> float:
        decimals = await self.get_decimals(token_name)
        return float(amount_in_wei / 10 ** decimals)

    async def get_smart_amount(
            self, settings: tuple = AMOUNT_PERCENT, need_percent: bool = False, token_name: str = None,
            omnicheck:bool = False,
    ) -> float:
        if not token_name:
            token_name = self.token

        if isinstance(settings[0], str) or need_percent:
            _, amount, _ = await self.get_token_balance(token_name, omnicheck=omnicheck)
            percent = round(random.uniform(float(settings[0]), float(settings[1])), 6) / 100
            amount = round(amount * percent, 6)
        else:
            amount = self.round_amount(*settings)
        return amount

    async def price_impact_defender(
            self, from_token_name: str, from_token_amount: float, to_token_name: str, to_token_amount_in_wei: int
    ):

        to_token_amount = await self.get_normalize_amount(to_token_name, to_token_amount_in_wei)

        token_info = {
            'DAI': 'dai',
            'USDT': 'tether',
            'USDC': 'usd-coin',
            'USDC.e': 'bridged-usdc-polygon-pos-bridge',
            'BUSD': 'binance-usd',
            'ETH': 'ethereum',
            'WETH': 'ethereum',
            'USDbC': 'bridged-usd-coin-base'
        }

        amount1_in_usd = (await self.get_token_price(token_info[from_token_name])) * from_token_amount
        amount2_in_usd = (await self.get_token_price(token_info[to_token_name])) * to_token_amount
        price_impact = 100 - (amount2_in_usd / amount1_in_usd) * 100

        if price_impact > PRICE_IMPACT:
            raise PriceImpactException(
                f'DEX price impact > your wanted impact | DEX impact: {price_impact:.3}% > Your impact {PRICE_IMPACT}%')

    async def get_bridge_data(self, chain_from_id: int, dapp_id: int):
        bridge_config, to_chain_ids, bridge_setting, bridge_token = {
            1: (CHAIN_IDS, ACROSS_CHAIN_ID_TO, ACROSS_BRIDGE_AMOUNT, ACROSS_TOKEN_NAME),
            2: (CHAIN_IDS, BUNGEE_CHAIN_ID_TO, BUNGEE_BRIDGE_AMOUNT, BUNGEE_TOKEN_NAME),
            3: (LAYERSWAP_CHAIN_NAME, LAYERSWAP_CHAIN_ID_TO, LAYERSWAP_BRIDGE_AMOUNT, LAYERSWAP_TOKEN_NAME),
            4: (CHAIN_IDS, NITRO_CHAIN_ID_TO, NITRO_BRIDGE_AMOUNT, NITRO_TOKEN_NAME),
            5: (ORBITER_CHAINS_INFO, ORBITER_CHAIN_ID_TO, ORBITER_BRIDGE_AMOUNT, ORBITER_TOKEN_NAME),
            6: (CHAIN_IDS, OWLTO_CHAIN_ID_TO, OWLTO_BRIDGE_AMOUNT, OWLTO_TOKEN_NAME),
            7: (CHAIN_IDS, RELAY_CHAIN_ID_TO, RELAY_BRIDGE_AMOUNT, RELAY_TOKEN_NAME),
            8: (RHINO_CHAIN_INFO, RHINO_CHAIN_ID_TO, RHINO_BRIDGE_AMOUNT, RHINO_TOKEN_NAME),
        }[dapp_id]

        source_chain = bridge_config[chain_from_id]
        dst_chain_id = random.choice(to_chain_ids)
        destination_chain = bridge_config[dst_chain_id]
        if isinstance(bridge_token, tuple):
            bridge_token = bridge_token[0]

        amount = await self.get_smart_amount(bridge_setting, token_name=bridge_token)
        return source_chain, destination_chain, amount, dst_chain_id

    async def new_client(self, chain_id: int):
        from functions import get_network_by_chain_id
        return Client(self.account_name, self.private_key, get_network_by_chain_id(chain_id), self.proxy_init)

    async def wait_for_receiving(
            self, chain_id: int, old_balance: int = 0, token_name: str = None, sleep_time: int = 60,
            check_balance_on_dst: bool = False, token_address: str = None, omnicheck:bool = False
    ) -> bool:
        client = await self.new_client(chain_id)
        if not token_name:
            token_name = self.token

        try:
            if check_balance_on_dst:
                if token_address:
                    old_balance, _, _ = await client.get_token_balance(
                        token_name=token_name, token_address=token_address, omnicheck=omnicheck
                    )
                else:
                    old_balance, _, _ = await client.get_token_balance(token_name, omnicheck=omnicheck)
                return old_balance

            self.logger_msg(*self.acc_info, msg=f'Waiting {token_name} to receive')

            new_eth_balance = 0
            while True:
                try:
                    if token_address:
                        new_eth_balance, _, _ = await client.get_token_balance(
                            token_name=token_name, token_address=token_address, omnicheck=omnicheck
                        )
                    else:
                        new_eth_balance, _, _ = await client.get_token_balance(token_name, omnicheck=omnicheck)
                except:
                    pass

                if new_eth_balance > old_balance:
                    decimals = 18
                    if token_name != client.network.token:
                        if token_address:
                            decimals = await client.get_decimals(token_address=token_address, omnicheck=omnicheck)
                        else:
                            decimals = await client.get_decimals(token_name, omnicheck=omnicheck)

                    amount = round((new_eth_balance - old_balance) / 10 ** decimals, 6)
                    self.logger_msg(*self.acc_info, msg=f'{amount} {token_name} was received', type_msg='success')
                    return True
                else:
                    self.logger_msg(*self.acc_info, msg=f'Still waiting {token_name} to receive...', type_msg='warning')
                    await asyncio.sleep(sleep_time)

        except Exception as error:
            raise SoftwareException(f'Error in <WAIT FOR RECEIVING> function. Error: {error}')
        finally:
            await client.session.close()

    async def get_token_balance(
            self, token_name: str = None, check_symbol: bool = True, omnicheck: bool = False,
            check_native: bool = False, token_address: str = None
    ) -> [float, int, str]:
        if not token_name:
            token_name = self.token

        await asyncio.sleep(3)
        if not check_native:
            if token_name != self.network.token:
                if token_address:
                    contract = self.get_contract(token_address)
                elif omnicheck:
                    contract = self.get_contract(TOKENS_PER_CHAIN2[self.network.name][token_name])
                else:
                    contract = self.get_contract(TOKENS_PER_CHAIN[self.network.name][token_name])

                amount_in_wei = await contract.functions.balanceOf(self.address).call()
                decimals = await contract.functions.decimals().call()

                if check_symbol:
                    symbol = await contract.functions.symbol().call()
                    return amount_in_wei, amount_in_wei / 10 ** decimals, symbol
                return amount_in_wei, amount_in_wei / 10 ** decimals, ''

        amount_in_wei = await self.w3.eth.get_balance(self.address)
        return amount_in_wei, amount_in_wei / 10 ** 18, self.network.token

    async def check_and_get_eth(self, settings: tuple = None) -> [float, int]:
        from functions import swap_odos, swap_oneinch, swap_izumi, swap_syncswap, swap_rango

        try:
            func = {
                'Base': [swap_rango, swap_izumi, swap_odos, swap_oneinch],
                'Linea': [swap_izumi, swap_syncswap],
                'Scroll': [swap_izumi, swap_syncswap],
                'zkSync': [swap_rango, swap_izumi, swap_syncswap, swap_odos, swap_oneinch]
            }[self.network.name]

            module_func = random.choice(func)
            data = await self.get_auto_amount(token_name_search='ETH')

            if data is False:
                self.logger_msg(*self.acc_info, msg=f'Not enough ETH! Launching swap module', type_msg='warning')
                await module_func(self.account_name, self.private_key, self.network, self.proxy_init, help_deposit=True)
        except:
            pass

        amount = await self.get_smart_amount(settings if settings else LIQUIDITY_AMOUNT)
        amount_in_wei = self.to_wei(amount)

        return amount, amount_in_wei

    async def get_auto_amount(self, token_name_search: str = None, class_name: str = None) -> [str, float, int]:

        wallet_balance = {k: await self.get_token_balance(k, False)
                          for k, v in TOKENS_PER_CHAIN[self.network.name].items()}
        valid_wallet_balance = {k: v[1] for k, v in wallet_balance.items() if v[0] != 0}
        eth_price = ETH_PRICE

        if 'ETH' in valid_wallet_balance:
            valid_wallet_balance['ETH'] = valid_wallet_balance['ETH'] * eth_price

        if 'WETH' in valid_wallet_balance:
            valid_wallet_balance['WETH'] = valid_wallet_balance['WETH'] * eth_price

        if sum(valid_wallet_balance.values()) > MIN_BALANCE * eth_price:

            valid_wallet_balance = {k: round(v, 7) for k, v in valid_wallet_balance.items()}

            biggest_token_balance_name = max(valid_wallet_balance, key=lambda x: valid_wallet_balance[x])

            if token_name_search == 'ETH' and biggest_token_balance_name != 'ETH':
                return False
            elif token_name_search == 'ETH' and biggest_token_balance_name == 'ETH':
                return True

            amount_from_token_on_balance = wallet_balance[biggest_token_balance_name][1]
            amount_from_token_on_balance_in_wei = wallet_balance[biggest_token_balance_name][0]

            token_names_list = list(filter(lambda token_name: token_name != biggest_token_balance_name,
                                           TOKENS_PER_CHAIN[self.network.name].keys()))

            if biggest_token_balance_name != 'WETH':
                token_names_list.remove('WETH')

            if biggest_token_balance_name == 'ETH':
                if GLOBAL_NETWORK == 11:
                    if class_name in ['Maverick', 'Rango']:
                        if 'USDT' in token_names_list:
                            token_names_list.remove('USDT')
                elif GLOBAL_NETWORK == 4:
                    if class_name in ['WooFi']:
                        if 'USDT' in token_names_list:
                            token_names_list.remove('USDT')
            else:
                token_names_list = ['ETH']

            random_to_token_name = random.choice(token_names_list)
            if not random_to_token_name:
                raise SoftwareException(f'No available pair from {biggest_token_balance_name}')

            if biggest_token_balance_name == 'ETH':
                percent = round(random.uniform(*AMOUNT_PERCENT), 9) / 100
            else:
                percent = 1

            amount = round(amount_from_token_on_balance * percent, 7)
            amount_in_wei = int(amount_from_token_on_balance_in_wei * percent)

            return biggest_token_balance_name, random_to_token_name, amount, amount_in_wei

        else:
            raise SoftwareException('Insufficient balance on account!')

    def get_contract(self, contract_address: str, abi: dict = ERC20_ABI) -> AsyncContract:
        return self.w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(contract_address),
            abi=abi
        )

    async def get_allowance(self, token_address: str, spender_address: str) -> int:
        contract = self.get_contract(token_address)
        return await contract.functions.allowance(
            self.address,
            spender_address
        ).call()

    async def get_priotiry_fee(self) -> int:
        fee_history = await self.w3.eth.fee_history(25, 'latest', [20.0])
        non_empty_block_priority_fees = [fee[0] for fee in fee_history["reward"] if fee[0] != 0]

        divisor_priority = max(len(non_empty_block_priority_fees), 1)

        priority_fee = int(round(sum(non_empty_block_priority_fees) / divisor_priority))

        return priority_fee

    async def prepare_transaction(self, value: int = 0) -> dict:
        try:
            tx_params = {
                'chainId': self.network.chain_id,
                'from': self.w3.to_checksum_address(self.address),
                'nonce': await self.w3.eth.get_transaction_count(self.address),
                'value': value,
            }

            if self.network.eip1559_support:

                base_fee = await self.w3.eth.gas_price
                max_priority_fee_per_gas = int(await self.get_priotiry_fee() * GAS_PRICE_MULTIPLIER)
                max_fee_per_gas = int(base_fee + max_priority_fee_per_gas * GAS_PRICE_MULTIPLIER)

                tx_params['maxPriorityFeePerGas'] = max_priority_fee_per_gas
                tx_params['maxFeePerGas'] = max_fee_per_gas
                tx_params['type'] = '0x2'
            else:
                if self.network.name == 'BNB Chain':
                    tx_params['gasPrice'] = self.w3.to_wei(round(random.uniform(1.4, 1.5), 1), 'gwei')
                else:
                    tx_params['gasPrice'] = int(await self.w3.eth.gas_price * GAS_PRICE_MULTIPLIER)

            return tx_params
        except Exception as error:
            raise BlockchainException(f'{self.get_normalize_error(error)}')

    async def make_approve(
            self, token_address: str, spender_address: str, amount_in_wei: int, unlimited_approve:bool
    ) -> bool:
        transaction = await self.get_contract(token_address).functions.approve(
            spender_address,
            amount=2 ** 256 - 1 if unlimited_approve else amount_in_wei
        ).build_transaction(await self.prepare_transaction())

        return await self.send_transaction(transaction)

    async def check_for_approved(self, token_address: str, spender_address: str, amount_in_wei: int,
                                 without_bal_check: bool = False, unlimited_approve:bool = UNLIMITED_APPROVE) -> bool:
        try:
            contract = self.get_contract(token_address)

            balance_in_wei = await contract.functions.balanceOf(self.address).call()
            symbol = await contract.functions.symbol().call()

            self.logger_msg(*self.acc_info, msg=f'Check for approval {symbol}')

            if not without_bal_check and balance_in_wei <= 0:
                raise SoftwareException(f'Zero {symbol} balance')

            approved_amount_in_wei = await self.get_allowance(
                token_address=token_address,
                spender_address=spender_address
            )

            if amount_in_wei <= approved_amount_in_wei:
                self.logger_msg(*self.acc_info, msg=f'Already approved')
                return False

            result = await self.make_approve(token_address, spender_address, amount_in_wei, unlimited_approve)

            await sleep(random.randint(5, 9))
            return result
        except Exception as error:
            raise BlockchainException(f'{self.get_normalize_error(error)}')

    async def send_transaction(
            self, transaction, need_hash: bool = False, without_gas: bool = False, poll_latency: int = 10,
            timeout: int = 360
    ) -> bool | HexStr:
        try:
            if not without_gas:
                transaction['gas'] = int((await self.w3.eth.estimate_gas(transaction)) * GAS_LIMIT_MULTIPLIER)
        except Exception as error:
            raise BlockchainException(f'{self.get_normalize_error(error)}')

        try:
            singed_tx = self.w3.eth.account.sign_transaction(transaction, self.private_key)
            tx_hash = self.w3.to_hex(await self.w3.eth.send_raw_transaction(singed_tx.rawTransaction))
        except Exception as error:
            if self.get_normalize_error(error) == 'already known':
                self.logger_msg(*self.acc_info, msg='RPC got error, but tx was send', type_msg='warning')
                return True
            else:
                raise BlockchainException(f'{self.get_normalize_error(error)}')

        total_time = 0
        timeout = timeout if self.network.name != 'Polygon' else 1200

        while True:
            try:
                receipts = await self.w3.eth.get_transaction_receipt(tx_hash)
                status = receipts.get("status")
                if status == 1:
                    message = f'Transaction was successful: {self.explorer}tx/{tx_hash}'
                    self.logger_msg(*self.acc_info, msg=message, type_msg='success')
                    if need_hash:
                        return tx_hash
                    return True
                elif status is None:
                    await asyncio.sleep(poll_latency)
                else:
                    raise BlockchainException(f'Transaction failed: {self.explorer}tx/{tx_hash}')
            except TransactionNotFound:
                if total_time > timeout:
                    raise BlockchainException(f"Transaction is not in the chain after {timeout} seconds")
                total_time += poll_latency
                await asyncio.sleep(poll_latency)

            except Exception as error:
                self.logger_msg(*self.acc_info, msg=f'RPC got autims response. Error: {error}', type_msg='warning')
                total_time += poll_latency
                await asyncio.sleep(poll_latency)

    async def get_token_price(self, token_name: str, vs_currency: str = 'usd') -> float:
        await asyncio.sleep(5)  # todo поправить на 10с
        url = 'https://api.coingecko.com/api/v3/simple/price'

        params = {'ids': f'{token_name}', 'vs_currencies': f'{vs_currency}'}

        async with self.session.get(url, params=params) as response:
            if response.status == 200:
                data = await response.json()
                return float(data[token_name][vs_currency])
            elif response.status == 429:
                self.logger_msg(
                    *self.acc_info, msg=f'CoinGecko API got rate limit. Next try in 60 second', type_msg='warning')
                await asyncio.sleep(60)
            raise SoftwareException(f'Bad request to CoinGecko API: {response.status}')

    async def wait_for_l0_received(self, tx_hash: HexStr) -> bool:
        if not WAIT_FOR_RECEIPT:
            return True

        url = f"https://api-mainnet.layerzero-scan.com/tx/{tx_hash}"

        while True:
            try:
                async with self.session.get(url=url) as response:
                    result = await response.json()

                    if (len(result["messages"]) > 0 and "dstTxHash" in result["messages"][0]
                            and "status" in result["messages"][0] and result["messages"][0]["status"] == "DELIVERED"):
                        self.logger_msg(
                            *self.acc_info, msg=f'Funds were received on destination chain', type_msg='success'
                        )
                        return True
            except Exception as error:
                self.logger_msg(
                    *self.acc_info, msg=f'Can`t get info about LayerZero transaction. Error: {error}',
                    type_msg='warning'
                )

            self.logger_msg(
                *self.acc_info, msg=f'Waiting for funds on destination chain...', type_msg='warning')
            await asyncio.sleep(30)

