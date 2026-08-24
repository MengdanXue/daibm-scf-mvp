pragma circom 2.2.3;

include "../node_modules/circomlib/circuits/poseidon.circom";
include "../node_modules/circomlib/circuits/comparators.circom";

template InvoiceLimit() {
    signal input invoiceAmount;
    signal input salt;
    signal input financingLimit;
    signal output commitment;

    // Both monetary values are unsigned 64-bit minor units. Explicit bit
    // decomposition prevents field-reduced wraparound witnesses.
    component amountBits = Num2Bits(64);
    component limitBits = Num2Bits(64);
    component lessEq = LessEqThan(64);
    component poseidon = Poseidon(2);

    amountBits.in <== invoiceAmount;
    limitBits.in <== financingLimit;
    lessEq.in[0] <== invoiceAmount;
    lessEq.in[1] <== financingLimit;
    lessEq.out === 1;

    poseidon.inputs[0] <== invoiceAmount;
    poseidon.inputs[1] <== salt;
    commitment <== poseidon.out;
}

// Circom emits output signals before declared public inputs, giving the
// stable ordered contract [commitment, financingLimit].
component main { public [financingLimit] } = InvoiceLimit();
