#!/usr/bin/env python3
# Copyright (c) 2009-2019 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Test mempool rebroadcast logic.

la de da de rebroadcasts are so much fun!!
"""

from decimal import Decimal
from test_framework.messages import (
        msg_mempool,
        msg_getdata,
        CInv
)
from test_framework.mininode import P2PInterface
from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import (
        assert_equal,
        wait_until,
        disconnect_nodes,
        connect_nodes,
        random_transaction
)
import pdb
import time

"""
* populate mempool with transactions
* calculate min of max fee rate -> assert correct
* block comes in
* check that the correct transactions are rebroadcasted
"""

class MempoolRebroadcastTest(BitcoinTestFramework):
    def set_test_params(self):
        self.num_nodes = 2

    def run_test(self):
        self.test_simple_rebroadcast()

    def test_simple_rebroadcast(self):
        self.log.info("Testing rebroadcast works")

        node1 = self.nodes[0]
        node2 = self.nodes[1]

        # start_time = int(time.time())
        # node1.setmocktime(start_time)
        # node2.setmocktime(start_time)

        # generate mempool transactions that both nodes know about
        for i in range(3):
            node1.sendtoaddress(node2.getnewaddress(), 4)

        self.sync_all()

        # check they both know about it
        assert_equal(len(node1.getrawmempool()), 3)
        assert_equal(len(node2.getrawmempool()), 3)

        disconnect_nodes(node1, 1)

        # generate mempool transactions that only node1 knows about
        for i in range(3):
            node1.sendtoaddress(node2.getnewaddress(), 5)

        # check that mempools are different
        assert_equal(len(node1.getrawmempool()), 6)
        assert_equal(len(node2.getrawmempool()), 3)

        # delta_time = 5 * 60 # seconds
        # node1.setmocktime(start_time + delta_time)
        # node2.setmocktime(start_time + delta_time)

        # reconnect the nodes
        connect_nodes(node1, 1)

        # NOTE: why is this only 5??? weird but ok.
        time.sleep(5)

        # check that node2 has gotten the txns since
        # they were rebroadcast
        assert_equal(len(node1.getrawmempool()), 6)
        assert_equal(len(node2.getrawmempool()), 6)

if __name__ == '__main__':
    MempoolRebroadcastTest().main()


# 2nd test: mempool > block size, check correct (top) txns rebroadcast
