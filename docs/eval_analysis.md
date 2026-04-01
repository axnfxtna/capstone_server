# Satu AI Brain — Evaluation Analysis
*Generated: 2026-03-30*

---

## Heatmap Analysis

The heatmap illustrates the high performance of the intent classifier, specifically highlighting the **95.5% accuracy** achieved:

- **Perfect Accuracy (1.00):** The system perfectly distinguishes Navigation, Farewell, and Out-of-Scope (OOS) queries. This ensures that the robot never mistakenly navigates away or ignores a safety boundary.

- **Minor Confusion:** There is a slight overlap between Chat and Info intents (0.08 and 0.05 respectively). This is common in natural language processing where conversational filler can sometimes resemble a request for information.

- **Safety Layer:** The 1.00 score for OOS indicates that the system never allows a non-domain query to trigger a response, meeting the strict 100% rejection requirement.

---

## Threshold Plot Analysis: SileroVAD (0.73)

The threshold plot justifies the choice of **0.73** for the SileroVAD coefficient — an engineering decision visual that demonstrates the value was optimized for the specific environment of Building E-12, not arbitrarily chosen.

### The Trade-off

| Curve | Behaviour |
|-------|-----------|
| **False Positives (orange)** | At low thresholds, the robot is too sensitive and interprets background noise in Building E-12 as speech. |
| **False Negatives (blue)** | At high thresholds, the robot becomes "deaf" to actual student speech, requiring students to raise their voice. |

### The Sweet Spot

The chosen value of **0.73** sits exactly where the orange curve (False Positives) has flattened out, ensuring the robot does not respond to itself or ambient noise, while keeping the blue curve (False Negatives) low enough to maintain a natural conversation flow.

### Efficiency vs. Accuracy — 70B vs. 8B Model

The precision-recall curve answers the question: *"Why not use a smaller, faster model?"*

- **Robustness:** Even at 100% Recall (capturing every single user intent), the **70B model** maintains over **90% Precision**.
- **8B model:** Drops toward **65% Precision** at high recall, which would lead to frequent misclassifications or wrong actions in production.

This makes the 70B model the only viable choice for a deployment environment where incorrect navigation or missed student queries carry a real cost.
