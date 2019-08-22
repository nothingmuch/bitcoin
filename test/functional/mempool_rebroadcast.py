#!/usr/bin/env python3
# Copyright (c) 2009-2019 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Test mempool rebroadcast logic.

"""

from collections import defaultdict
from test_framework.mininode import P2PInterface, mininode_lock
from test_framework.test_framework import BitcoinTestFramework
from test_framework.messages import (
        msg_mempool,
        msg_getdata,
        CInv
)
from test_framework.util import (
        assert_equal,
        assert_greater_than,
        assert_greater_than_or_equal,
        wait_until,
        disconnect_nodes,
        connect_nodes,
        random_transaction,
        gen_return_txouts,
        create_confirmed_utxos,
        create_lots_of_big_transactions,
)
import time

# Constant from net_processing
MAX_REBROADCAST_WEIGHT = 3000000

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

class MempoolRebroadcastTest(BitcoinTestFramework):
    def set_test_params(self):
        self.num_nodes = 2
        self.extra_args = [[
            "-acceptnonstdtxn=1",
            "-blockmaxweight=3000000",
            "-whitelist=127.0.0.1"
            ]] * self.num_nodes

    def run_test(self):
        self.test_simple_rebroadcast()
        self.test_correct_invs()
        self.test_rebroadcast_top_txns()
        self.test_recency_filter()

    # helper method that uses getblocktemplate with node arg
    # set to MAX_REBROADCAST_WEIGHT to find txns expected to
    # be rebroadcast
    def find_top_txns(self, node):
        tmpl = node.getblocktemplate({'rules': ['segwit']})

        tx_hshs = []
        for tx in tmpl['transactions']:
            tx_hshs.append(tx['hash'])

        return tx_hshs

    def compare_txns_to_invs(self, txn_hshs, invs):
        tx_ids = [int(txhsh, 16) for txhsh in txn_hshs]

        assert_equal(len(tx_ids), len(invs))
        assert_equal(tx_ids.sort(), invs.sort())

    def test_simple_rebroadcast(self):
        self.log.info("Test simplest rebroadcast case")

        node1 = self.nodes[0]
        node2 = self.nodes[1]

        # generate mempool transactions that both nodes know about
        for i in range(3):
            node1.sendtoaddress(node2.getnewaddress(), 4)

        self.sync_all()
        disconnect_nodes(node1, 1)

        # generate mempool transactions that only node1 knows about
        for i in range(3):
            node1.sendtoaddress(node2.getnewaddress(), 5)

        # check that mempools are different
        assert_equal(len(node1.getrawmempool()), 6)
        assert_equal(len(node2.getrawmempool()), 3)

        # bump mocktime 30 minutes to make sure the txns
        # are not excluded from rebroadcast due to recency
        mocktime = int(time.time()) + 31 * 60
        node1.setmocktime(mocktime)
        node2.setmocktime(mocktime)

        # reconnect the bitcoin nodes
        connect_nodes(node1, 1)
        time.sleep(1)
        mocktime += 300 * 60 # hit rebroadcast interval
        node1.setmocktime(mocktime)
        node2.setmocktime(mocktime)
        # this sleep is needed to ensure the invs get sent
        # before we bump the mocktime because of nNextInvSend
        time.sleep(0.5)

        # bump by GETDATA interval
        mocktime += 60
        node1.setmocktime(mocktime)
        node2.setmocktime(mocktime)

        # check that node2 got txns bc rebroadcasting
        wait_until(lambda: len(node2.getrawmempool()) == 6, timeout=30)

    def test_correct_invs(self):
        self.log.info("Test that expected invs are rebroadcast")

        node = self.nodes[0]
        node.setmocktime(0)

        # mine a block to clear out the mempool
        node.generate(1)
        assert_equal(len(node.getrawmempool()), 0)

        # add p2p connection
        conn = node.add_p2p_connection(P2PStoreTxInvs())

        # create txns
        for i in range(3):
            node.sendtoaddress(node.getnewaddress(), 2)
        assert_equal(len(node.getrawmempool()), 3)

        # bump mocktime to ensure the txns won't be excluded due to recency filter
        mocktime = int(time.time()) + 31 * 60
        node.setmocktime(mocktime)

        # add another p2p connection since txns aren't rebroadcast to the same peer (see filterInventoryKnown)
        conn2 = node.add_p2p_connection(P2PStoreTxInvs())

        # bump mocktime of node1 so rebroadcast is triggered
        mocktime += 300 * 60 # hit rebroadcast interval
        node.setmocktime(mocktime)

        # `nNextInvSend` delay on `setInventoryTxToSend
        wait_until(lambda: conn2.get_invs(), timeout=30)

        # verify correct invs were sent
        self.compare_txns_to_invs(node.getrawmempool(), conn2.get_invs())

    def test_rebroadcast_top_txns(self):
        self.log.info("Testing that only txns with top fee rate get rebroadcast")

        node = self.nodes[0]
        node.setmocktime(0)

        # mine a block to clear out the mempool
        node.generate(1)
        assert_equal(len(node.getrawmempool()), 0)

        conn1 = node.add_p2p_connection(P2PStoreTxInvs())

        # create txns
        min_relay_fee = node.getnetworkinfo()["relayfee"]
        txouts = gen_return_txouts()
        utxo_count = 90
        utxos = create_confirmed_utxos(min_relay_fee, node, utxo_count)
        base_fee = min_relay_fee*100 # our transactions are smaller than 100kb
        txids = []

        # Create 3 batches of transactions at 3 different fee rate levels
        range_size = utxo_count // 3

        for i in range(3):
            txids.append([])
            start_range = i * range_size
            end_range = start_range + range_size
            txids[i] = create_lots_of_big_transactions(node, txouts, utxos[start_range:end_range], end_range - start_range, (i+1)*base_fee)

        # 90 transactions should be created
        # confirm the invs were sent (initial broadcast)
        assert_equal(len(node.getrawmempool()), 90)
        wait_until(lambda: len(conn1.tx_invs_received) == 90)

        # confirm txns are more than max rebroadcast amount
        assert_greater_than(node.getmempoolinfo()['bytes'], MAX_REBROADCAST_WEIGHT)

        self.sync_all()

        # age txns to ensure they won't be excluded due to recency filter
        mocktime = int(time.time()) + 31 * 60
        node.setmocktime(mocktime)

        # add another p2p connection since txns aren't rebroadcast to the same peer (see filterInventoryKnown)
        conn2 = node.add_p2p_connection(P2PStoreTxInvs())

        # trigger rebroadcast to occur
        mocktime += 300 * 60 # seconds
        node.setmocktime(mocktime)
        time.sleep(1) # ensure send message thread runs so invs get sent

        inv_count = len(conn2.get_invs())
        assert_greater_than(inv_count, 0)

        # confirm that the correct txns were rebroadcast
        self.compare_txns_to_invs(self.find_top_txns(node), conn2.get_invs())

    def test_recency_filter(self):
        self.log.info("Test recent txns don't get rebroadcast")

        node = self.nodes[0]
        node2 = self.nodes[1]

        node.setmocktime(0)

        # mine blocks to clear out the mempool
        node.generate(10)
        assert_equal(len(node.getrawmempool()), 0)

        # add p2p connection
        conn = node.add_p2p_connection(P2PStoreTxInvs())

        # create old txn
        old_txn = node.sendtoaddress(node.getnewaddress(), 2)
        assert_equal(len(node.getrawmempool()), 1)
        wait_until(lambda: conn.get_invs(), timeout=30)

        # bump mocktime to ensure the txn is old
        mocktime = int(time.time()) + 31 * 60 # seconds
        node.setmocktime(mocktime)

        delta_time = 28 * 60 # seconds
        while True:
            # create a recent transaction
            new_tx = node2.sendtoaddress(node2.getnewaddress(), 2)
            new_tx_id = int(new_tx, 16)

            # ensure node1 has the transaction
            wait_until(lambda: new_tx in node.getrawmempool())

            # add another p2p connection since txns aren't rebroadcast
            # to the same peer (see filterInventoryKnown)
            new_conn = node.add_p2p_connection(P2PStoreTxInvs())

            # bump mocktime to try to get rebroadcast,
            # but not so much that the txn would be old
            mocktime += delta_time
            node.setmocktime(mocktime)

            time.sleep(1.1)

            # once we get any rebroadcasts, ensure the most recent txn is not included
            if new_conn.get_invs():
                assert(new_tx_id not in new_conn.get_invs())
                break

if __name__ == '__main__':
    MempoolRebroadcastTest().main()

