"""Turning interpreted rows into cases, owners and leads.

The parser reports what a county published. This package decides what to do about it: a
case is written for every row, an owner for every published name, a compliance verdict for
every case -- and a lead only where all of those line up in favour of contacting someone.
"""
