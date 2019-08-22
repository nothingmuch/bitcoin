#!/usr/bin/env python3
# Copyright (c) 2009-2019 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""
Ensure that wallet transactions get succesfully broadcast to at least one peer.
"""

from collections import defaultdict
from test_framework.mininode import P2PInterface, mininode_lock
from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import (
        assert_equal,
        assert_greater_than,
        wait_until,
        create_lots_of_big_transactions,
        create_confirmed_utxos,
        gen_return_txouts,
)
import time

class P2PStoreTxInvs(P2PInterface):
    def __init__(self):
        super().__init__()
        self.tx_invs_received = defaultdict(int)

    def on_inv(self, message):
        # Store how many times invs have been received for each tx.
        for i in message.inv:
            if i.type == 1:
                # save txid
                self.tx_invs_received[i.hash] += 1

    def get_invs(self):
        with mininode_lock:
            return list(self.tx_invs_received.keys())

# Constant from net_processing
MAX_REBROADCAST_WEIGHT = 3000000

class MempoolWalletTransactionsTest(BitcoinTestFramework):
    def set_test_params(self):
        self.num_nodes = 2
        self.extra_args = [["-whitelist=127.0.0.1", "-acceptnonstdtxn=1"]] * self.num_nodes

    def compare_txns_to_invs(self, txn_hshs, invs):
        tx_ids = [int(txhsh, 16) for txhsh in txn_hshs]

        assert_equal(len(tx_ids), len(invs))
        assert_equal(tx_ids.sort(), invs.sort())

    def run_test(self):
        self.log.info("test that mempool will ensure initial broadcast of wallet txns")

        node = self.nodes[0]

        # generate top of mempool txns
        min_relay_fee = node.getnetworkinfo()["relayfee"]
        txouts = gen_return_txouts()
        utxo_count = 90
        utxos = create_confirmed_utxos(min_relay_fee, node, utxo_count)
        base_fee = min_relay_fee*100 # our transactions are smaller than 100kb

        txids = create_lots_of_big_transactions(node, txouts, utxos, 90, 3*base_fee)

        # check fee rate of these txns for comparison
        txid = txids[0]
        entry = node.getmempoolentry(txid)
        high_fee_rate = entry['fee'] / entry['vsize']

        # confirm txns are more than max rebroadcast amount
        assert_greater_than(node.getmempoolinfo()['bytes'], MAX_REBROADCAST_WEIGHT)

        # generate a wallet txn that will be broadcast to nobody
        us0 = create_confirmed_utxos(min_relay_fee, node, 1).pop()
        inputs = [{ "txid" : us0["txid"], "vout" : us0["vout"]}]
        outputs = {node.getnewaddress() : 0.0001}
        tx = node.createrawtransaction(inputs, outputs)
        node.settxfee(min_relay_fee) # specifically fund this tx with low fee
        txF = node.fundrawtransaction(tx)
        txFS = node.signrawtransactionwithwallet(txF['hex'])
        wallettxid = node.sendrawtransaction(txFS['hex'])  # txhsh in hex

        # ensure the wallet txn has a low fee rate & thus wont be
        # rebroadcast due to top-of-mempool rule
        walletentry = node.getmempoolentry(wallettxid)
        low_fee_rate = walletentry['fee'] / walletentry['vsize']
        assert_greater_than(high_fee_rate, low_fee_rate)

        # add p2p connection
        conn = node.add_p2p_connection(P2PStoreTxInvs())

        # bump mocktime of node1 so rebroadcast is triggered
        mocktime = int(time.time()) + 300 * 60 # hit rebroadcast interval
        node.setmocktime(mocktime)

        # `nNextInvSend` delay on `setInventoryTxToSend
        wait_until(lambda: conn.get_invs(), timeout=30)

        # verify the wallet txn inv was sent due to mempool tracking
        wallettxinv = int(wallettxid, 16)
        assert_equal(wallettxinv in conn.get_invs(), True)

if __name__ == '__main__':
    MempoolWalletTransactionsTest().main()

