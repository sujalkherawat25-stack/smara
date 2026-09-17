#!/bin/sh
set -eu
xwd -root -silent | convert xwd:- png:-
